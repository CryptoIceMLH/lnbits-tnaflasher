/*
 * rp2350-flasher.js — flashes an RP2350 (ARM-S) .uf2 in the browser over WebUSB
 * using the bootrom's PICOBOOT interface. The board must be in BOOT mode: hold
 * BOOT, plug in USB, and it appears as 2E8A:000F.
 *
 * Protocol source: pico-sdk boot/picoboot.h, with command sequencing taken from
 * raspberrypi/picotool (reset -> exclusive -> exit XIP -> erase -> write -> read
 * back -> REBOOT2) and the pico-bootrom-rp2350 PICOBOOT handler.
 *
 * Hard rules:
 *   - Erase only the 4 KiB sectors the image occupies, never the whole chip.
 *     Firmware keeps its settings near the end of flash.
 *   - Skip every block that isn't ARM-S main-flash data. That includes the
 *     RP2350-E10 absolute-family block at 0x10FFFF00, which picotool also ignores.
 *   - Read back and compare before rebooting.
 *   - No OTP commands exist in this file. OTP writes are permanent.
 */

(function (global) {
  "use strict";

  const RP2350_VID = 0x2e8a;
  const RP2350_PID = 0x000f;

  // ---- UF2 format ----
  const UF2_BLOCK_SIZE = 512;
  const UF2_MAGIC_START0 = 0x0a324655;
  const UF2_MAGIC_START1 = 0x9e5d5157;
  const UF2_MAGIC_END = 0x0ab16f30;
  const UF2_FLAG_NOT_MAIN_FLASH = 0x00000001;
  const UF2_FLAG_FAMILY_ID_PRESENT = 0x00002000;
  const FAMILY_RP2350_ARM_S = 0xe48bff59;

  // ---- Flash layout ----
  const FLASH_BASE = 0x10000000;
  const FLASH_END = 0x11000000; // end of the 16 MiB XIP window
  const PAGE_SIZE = 256;
  const SECTOR_SIZE = 4096;

  // ---- PICOBOOT (boot/picoboot.h) ----
  const PICOBOOT_MAGIC = 0x431fd10b;
  const IF_RESET = 0x41;
  const IF_CMD_STATUS = 0x42;
  const PC_EXCLUSIVE_ACCESS = 0x01;
  const PC_FLASH_ERASE = 0x03;
  const PC_READ = 0x84;
  const PC_WRITE = 0x05;
  const PC_EXIT_XIP = 0x06;
  const PC_REBOOT2 = 0x0a;
  const EXCLUSIVE = 1; // disables USB drive writes while we flash
  const REBOOT2_FLAG_REBOOT_TYPE_FLASH_UPDATE = 0x4;
  const REBOOT_DELAY_MS = 500;
  const TRANSFER_TIMEOUT_MS = 10000; // picotool's default bulk timeout

  const STATUS_NAMES = [
    "OK", "UNKNOWN_CMD", "INVALID_CMD_LENGTH", "INVALID_TRANSFER_LENGTH",
    "INVALID_ADDRESS", "BAD_ALIGNMENT", "INTERLEAVED_WRITE", "REBOOTING",
    "UNKNOWN_ERROR", "INVALID_STATE", "NOT_PERMITTED", "INVALID_ARG",
    "BUFFER_TOO_SMALL", "PRECONDITION_NOT_MET", "MODIFIED_DATA", "INVALID_DATA",
    "NOT_FOUND", "UNSUPPORTED_MODIFICATION",
  ];

  const hex = (n) => "0x" + (n >>> 0).toString(16).padStart(8, "0");

  /**
   * Parse a .uf2 into the sectors to erase and the data runs to write in each.
   * Throws on any corrupt, truncated or unsuitable file, so bad input is caught
   * before flash is touched.
   * Returns { sectors: [{ addr, runs: [{ addr, data }] }], imageBytes, skipped }.
   */
  function parseUf2(buffer) {
    const bytes = new Uint8Array(buffer);
    if (bytes.length === 0 || bytes.length % UF2_BLOCK_SIZE !== 0) {
      throw new Error("Not a valid UF2: size is not a multiple of 512 bytes");
    }
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const pages = new Map(); // flash address -> 256-byte payload
    const seenBlockNos = new Set();
    let expectedBlocks = null;
    let skipped = 0;

    for (let off = 0; off < bytes.length; off += UF2_BLOCK_SIZE) {
      const blockIdx = off / UF2_BLOCK_SIZE;
      if (
        view.getUint32(off, true) !== UF2_MAGIC_START0 ||
        view.getUint32(off + 4, true) !== UF2_MAGIC_START1 ||
        view.getUint32(off + 508, true) !== UF2_MAGIC_END
      ) {
        throw new Error("Not a valid UF2: bad magic in block " + blockIdx);
      }
      const flags = view.getUint32(off + 8, true);
      const family = view.getUint32(off + 28, true);
      const isImage =
        (flags & UF2_FLAG_FAMILY_ID_PRESENT) !== 0 &&
        (flags & UF2_FLAG_NOT_MAIN_FLASH) === 0 &&
        family === FAMILY_RP2350_ARM_S;
      if (!isImage) {
        skipped++;
        continue;
      }

      const addr = view.getUint32(off + 12, true);
      const size = view.getUint32(off + 16, true);
      const blockNo = view.getUint32(off + 20, true);
      const numBlocks = view.getUint32(off + 24, true);
      if (size !== PAGE_SIZE) {
        throw new Error("Unsupported UF2: block " + blockIdx + " payload is not 256 bytes");
      }
      if (addr % PAGE_SIZE !== 0 || addr < FLASH_BASE || addr + PAGE_SIZE > FLASH_END) {
        throw new Error("Unsupported UF2: bad flash address " + hex(addr));
      }
      if (pages.has(addr)) {
        throw new Error("Invalid UF2: address " + hex(addr) + " appears twice");
      }
      if (expectedBlocks === null) expectedBlocks = numBlocks;
      if (numBlocks !== expectedBlocks || blockNo >= numBlocks || seenBlockNos.has(blockNo)) {
        throw new Error("Invalid UF2: inconsistent block numbering at block " + blockIdx);
      }
      seenBlockNos.add(blockNo);
      pages.set(addr, bytes.subarray(off + 32, off + 32 + PAGE_SIZE));
    }

    if (pages.size === 0) {
      throw new Error("This UF2 has no RP2350 ARM firmware in it");
    }
    if (pages.size !== expectedBlocks) {
      throw new Error("Incomplete UF2: found " + pages.size + " of " + expectedBlocks + " blocks");
    }
    if (!pages.has(FLASH_BASE)) {
      throw new Error("Unsupported UF2: image does not start at " + hex(FLASH_BASE));
    }

    // Group contiguous pages into runs, split at sector boundaries.
    const sectors = [];
    let sector = null;
    let run = null;
    for (const addr of Array.from(pages.keys()).sort((a, b) => a - b)) {
      const sectorAddr = addr - (addr % SECTOR_SIZE);
      if (!sector || sector.addr !== sectorAddr) {
        sector = { addr: sectorAddr, runs: [] };
        sectors.push(sector);
        run = null;
      }
      if (!run || run.addr + run.chunks.length * PAGE_SIZE !== addr) {
        run = { addr, chunks: [] };
        sector.runs.push(run);
      }
      run.chunks.push(pages.get(addr));
    }
    for (const s of sectors) {
      s.runs = s.runs.map((r) => {
        const data = new Uint8Array(r.chunks.length * PAGE_SIZE);
        r.chunks.forEach((c, i) => data.set(c, i * PAGE_SIZE));
        return { addr: r.addr, data };
      });
    }
    return { sectors, imageBytes: pages.size * PAGE_SIZE, skipped };
  }

  /**
   * Drives one RP2350 in BOOT mode. Call requestDevice() from a user gesture,
   * then connectAndVerify() (touches no flash), then flashImage(uf2Buffer).
   */
  class RP2350Flasher {
    constructor(opts = {}) {
      this.device = null;
      this.iface = null;
      this.epIn = null;
      this.epOut = null;
      this.epInPacketSize = 64;
      this.token = 1;
      this._rebooted = false;
      this.onLog = opts.onLog || (() => {});
      this.onProgress = opts.onProgress || (() => {});
    }

    log(m) {
      this.onLog(m);
    }

    async requestDevice() {
      this.device = await navigator.usb.requestDevice({
        filters: [{ vendorId: RP2350_VID, productId: RP2350_PID }],
      });
      this.log("selected device: " + (this.device.productName || "(unnamed)") + " — 2E8A:000F");
      return this.device;
    }

    /** Open and claim PICOBOOT, then prove the bootrom answers. Writes no flash. */
    async connectAndVerify() {
      const d = this.device;
      if (!d) throw new Error("No device selected");
      await d.open();
      if (!d.configuration) await d.selectConfiguration(1);
      this._findPicobootInterface();
      await d.claimInterface(this.iface);
      this.log("claimed PICOBOOT interface " + this.iface);
      await this._reset();
      await this._cmd(PC_EXCLUSIVE_ACCESS, new Uint8Array([EXCLUSIVE]));
      this.log("bootrom responded; USB drive writes locked for the flash");
    }

    /** Parse, erase, write, read back, reboot. Rejects before flash on a bad file. */
    async flashImage(uf2Buffer) {
      const image = parseUf2(uf2Buffer);
      const total = image.sectors.length;
      this.log(
        "image: " + image.imageBytes + " bytes in " + total + " sectors (" +
          image.skipped + " non-image block skipped)"
      );

      await this._cmd(PC_EXIT_XIP);
      for (let i = 0; i < total; i++) {
        const s = image.sectors[i];
        await this._cmd(PC_FLASH_ERASE, this._range(s.addr, SECTOR_SIZE));
        for (const r of s.runs) {
          await this._cmd(PC_WRITE, this._range(r.addr, r.data.length), { data: r.data });
        }
        this.onProgress({ phase: "write", current: i + 1, total });
      }

      await this._cmd(PC_EXIT_XIP);
      for (let i = 0; i < total; i++) {
        for (const r of image.sectors[i].runs) {
          const got = await this._cmd(PC_READ, this._range(r.addr, r.data.length), {
            readLen: r.data.length,
          });
          for (let b = 0; b < r.data.length; b++) {
            if (got[b] !== r.data[b]) {
              throw new Error("Verify failed at " + hex(r.addr + b) + " — flash did not match the image");
            }
          }
        }
        this.onProgress({ phase: "verify", current: i + 1, total });
      }
      this.log("verify OK");

      const args = new Uint8Array(16);
      const v = new DataView(args.buffer);
      v.setUint32(0, REBOOT2_FLAG_REBOOT_TYPE_FLASH_UPDATE, true);
      v.setUint32(4, REBOOT_DELAY_MS, true);
      v.setUint32(8, FLASH_BASE, true);
      // The image is already verified, so a lost ACK here is not a failed flash.
      try {
        await this._cmd(PC_REBOOT2, args);
        this.log("rebooting into the new firmware");
      } catch (e) {
        this.log("reboot not acknowledged (" + (e.message || e) + "). If the board stays on the BOOT screen, unplug and replug it.");
      }
      this._rebooted = true;
      await this._close();
    }

    _findPicobootInterface() {
      for (const itf of this.device.configuration.interfaces) {
        const alt = itf.alternates[0];
        if (alt.interfaceClass !== 0xff) continue;
        const bulk = alt.endpoints.filter((e) => e.type === "bulk");
        const inEp = bulk.find((e) => e.direction === "in");
        const outEp = bulk.find((e) => e.direction === "out");
        if (inEp && outEp) {
          this.iface = itf.interfaceNumber;
          this.epIn = inEp.endpointNumber;
          this.epOut = outEp.endpointNumber;
          this.epInPacketSize = inEp.packetSize || 64;
          return;
        }
      }
      throw new Error("PICOBOOT interface not found. Re-enter BOOT mode and try again.");
    }

    _range(addr, size) {
      const a = new Uint8Array(8);
      const v = new DataView(a.buffer);
      v.setUint32(0, addr, true);
      v.setUint32(4, size, true);
      return a;
    }

    _ctrlSetup(request) {
      return { requestType: "vendor", recipient: "interface", request, value: 0, index: this.iface };
    }

    async _reset() {
      const r = await this.device.controlTransferOut(this._ctrlSetup(IF_RESET));
      if (r.status !== "ok") throw new Error("PICOBOOT reset failed: " + r.status);
    }

    async _timed(promise, what) {
      let timer;
      const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(what + " timed out — check the USB cable")), TRANSFER_TIMEOUT_MS);
      });
      try {
        return await Promise.race([promise, timeout]);
      } finally {
        clearTimeout(timer);
      }
    }

    /** Device stalled: read why, clear the stall, then throw a readable error. */
    async _raiseStall(cmdId, ep, direction) {
      let reason = "stalled";
      try {
        const st = await this.device.controlTransferIn(this._ctrlSetup(IF_CMD_STATUS), 16);
        if (st.status === "ok" && st.data && st.data.byteLength >= 8) {
          const code = st.data.getUint32(4, true);
          reason = STATUS_NAMES[code] || "status " + code;
        }
        await this.device.clearHalt(direction, ep);
        await this._reset();
      } catch (_) {
        // Keep the original failure; the caller closes the device.
      }
      throw new Error("Device rejected command 0x" + cmdId.toString(16) + ": " + reason);
    }

    async _out(cmdId, payload, what) {
      const r = await this._timed(this.device.transferOut(this.epOut, payload), what);
      if (r.status === "stall") await this._raiseStall(cmdId, this.epOut, "out");
      if (r.status !== "ok") throw new Error(what + " failed: " + r.status);
    }

    async _in(cmdId, length, what) {
      const r = await this._timed(this.device.transferIn(this.epIn, length), what);
      if (r.status === "stall") await this._raiseStall(cmdId, this.epIn, "in");
      if (r.status !== "ok") throw new Error(what + " failed: " + r.status);
      return r.data;
    }

    /** One PICOBOOT command: 32-byte header, optional data, opposite-direction ACK. */
    async _cmd(cmdId, args = new Uint8Array(0), { data = null, readLen = 0 } = {}) {
      const isIn = (cmdId & 0x80) !== 0;
      const transferLength = isIn ? readLen : data ? data.length : 0;
      const pkt = new Uint8Array(32);
      const v = new DataView(pkt.buffer);
      v.setUint32(0, PICOBOOT_MAGIC, true);
      v.setUint32(4, this.token++, true);
      v.setUint8(8, cmdId);
      v.setUint8(9, args.length);
      v.setUint32(12, transferLength, true);
      pkt.set(args, 16);
      await this._out(cmdId, pkt, "command");

      let result = null;
      if (isIn && readLen) {
        result = new Uint8Array(readLen);
        let got = 0;
        while (got < readLen) {
          const chunk = await this._in(cmdId, readLen - got, "read");
          if (chunk.byteLength === 0) throw new Error("read returned no data at offset " + got);
          result.set(new Uint8Array(chunk.buffer, chunk.byteOffset, chunk.byteLength), got);
          got += chunk.byteLength;
        }
      } else if (data) {
        await this._out(cmdId, data, "write");
      }

      if (isIn) {
        await this._out(cmdId, new Uint8Array(0), "ack");
      } else {
        await this._in(cmdId, this.epInPacketSize, "ack");
      }
      return result;
    }

    async _close() {
      const d = this.device;
      if (!d) return;
      // Clearing exclusive access gives the USB drive back after a failed flash.
      if (!this._rebooted && this.iface !== null) {
        try { await this._reset(); } catch (_) {}
      }
      try { if (this.iface !== null) await d.releaseInterface(this.iface); } catch (_) {}
      try { await d.close(); } catch (_) {}
    }
  }

  RP2350Flasher.parseUf2 = parseUf2;
  global.RP2350Flasher = RP2350Flasher;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { RP2350Flasher, parseUf2 };
  }
})(typeof window !== "undefined" ? window : globalThis);

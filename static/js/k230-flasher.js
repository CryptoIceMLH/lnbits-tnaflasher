/*
 * k230-flasher.js — flashes a Canaan K230 (Nano 3s) .kdimg in the browser over
 * WebUSB (navigator.usb). This is a direct port of the firmware side's
 * k230_flash Python package (burners.py + usb_utils.py) — the KBURN protocol.
 *
 * NOT WebSerial / esptool. The K230 enumerates as a raw USB device
 * (VID 0x29F1, PID 0x0230) and speaks KBURN over bulk endpoints + a few EP0
 * vendor control transfers. The browser claims it with WebUSB.
 *
 * Hard rules (these prevent bricking — see NANO3S-FLASHER-HANDOFF.md §2.3 / §C):
 *   - FULL-RANGE UBI erase, never tail-only.
 *   - One device handle for the whole flow.
 *   - Per-partition SHA-256 verify stays ON (done in kdimg-parser.readPartData).
 *
 * Maps Python -> WebUSB:
 *   dev.write(ep_out, buf)            -> device.transferOut(epOut, buf)
 *   dev.read(ep_in, len)             -> device.transferIn(epIn, len)
 *   dev.ctrl_transfer(CTRL_OUT|VND…) -> device.controlTransferOut({...})
 *   dev.ctrl_transfer(CTRL_IN |VND…) -> device.controlTransferIn({...}, len)
 *
 * Depends on kdimg-parser.js (parseKdimg, readPartData) being loaded first.
 */

(function (global) {
  "use strict";

  // ---- USB identity ----
  const K230_VID = 0x29f1;
  const K230_PID = 0x0230;

  // ---- Device modes ----
  const DEV_INVALID = 0;
  const DEV_BROM = 1;
  const DEV_UBOOT = 2;

  // ---- EP0 vendor requests (usb_utils.py) ----
  const EP0_GET_CPU_INFO = 0;
  const EP0_SET_DATA_ADDRESS = 1;
  // const EP0_SET_DATA_LENGTH = 2;  // unused
  const EP0_PROG_START = 4;

  // ---- KBURN packet framing (burners.py) ----
  const PACKET_SIZE = 60;
  const HEADER_SIZE = 6;
  const MAX_DATA_SIZE = PACKET_SIZE - HEADER_SIZE; // 54
  const CMD_FLAG_DEV_TO_HOST = 0x8000;
  const KBURN_RESULT_OK = 0x1;

  // ---- Command codes ----
  const CMD_NONE = 0x00;
  const CMD_REBOOT = 0x01;
  const CMD_DEV_PROBE = 0x10;
  const CMD_DEV_GET_INFO = 0x11;
  const CMD_ERASE_LBA = 0x20;
  const CMD_WRITE_LBA = 0x21;

  // ---- Media type ----
  const MEDIUM_SPI_NAND = 3;

  const LOADER_ADDRESS = 0x80360000;
  const BROM_PAGE_SIZE = 1000; // K230_SRAM_PAGE_SIZE — loader upload chunk
  const REBOOT_MARK = 0x52626f74; // "tboR" — reboot marker

  // Persistent data partition (NAND ground truth, NOT /proc/mtd). Used only as
  // a fallback for kdimgs that omit the data partition (see HANDOFF §B).
  const DATA_PART_OFFSET = 0x0c800000;
  const DATA_PART_ERASE = 0x03800000;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function littleEndianU16(n) {
    return new Uint8Array([n & 0xff, (n >> 8) & 0xff]);
  }

  // Build a u64 little-endian byte array from a JS Number (offsets/sizes < 2^53).
  function u64le(n) {
    const out = new Uint8Array(8);
    let v = n;
    for (let i = 0; i < 8; i++) {
      out[i] = v & 0xff;
      v = Math.floor(v / 256);
    }
    return out;
  }

  /**
   * K230Flasher drives one WebUSB device through the whole KBURN flow.
   * Construct with an onLog(msg) and onProgress({phase, partName, current,
   * total, partsDone, partsTotal}) callback, then call flash(kdimgArrayBuffer,
   * loaderArrayBuffer).
   */
  class K230Flasher {
    constructor(opts = {}) {
      this.device = null;
      this.iface = 0;
      this.epIn = null; // endpoint number
      this.epOut = null;
      this.outChunkSize = 512; // overwritten by probe()
      this.inChunkSize = 512;
      this.epInPacketSize = 512; // max packet size of the bulk IN endpoint
      this.blkSz = 512;
      this.capacity = 0;
      this._verifiedMode = null; // set by connectAndVerify()
      this._debug = opts.debug !== false; // verbose protocol logging (default on while stabilising)
      this.onLog = opts.onLog || (() => {});
      this.onProgress = opts.onProgress || (() => {});
    }

    log(m) {
      this.onLog(m);
    }

    // ---- Device acquisition ------------------------------------------------

    static isSupported() {
      return typeof navigator !== "undefined" && !!navigator.usb;
    }

    /** Prompt the user to pick the K230 (must be called from a user gesture).
     *  Both BROM and LOADER ("USB download gadget") modes enumerate as the SAME
     *  29F1:0230 (confirmed on hardware), so we always filter to that — the
     *  picker shows only the miner, never the user's mouse/mic/etc. */
    async requestDevice() {
      this.device = await navigator.usb.requestDevice({
        filters: [{ vendorId: K230_VID, productId: K230_PID }],
      });
      const d = this.device;
      this.log(
        "selected device: " +
          (d.productName || "(unnamed)") + " — VID:PID " +
          d.vendorId.toString(16).padStart(4, "0") + ":" +
          d.productId.toString(16).padStart(4, "0")
      );
      return this.device;
    }

    /** Try to reacquire an already-granted device without a prompt (used after
     *  the BROM->loader re-enumeration). Falls back to null if not found. */
    async _findGrantedDevice() {
      const devs = await navigator.usb.getDevices();
      return (
        devs.find((d) => d.vendorId === K230_VID && d.productId === K230_PID) ||
        null
      );
    }

    /** Poll getDevices() (and listen for the WebUSB `connect` event) for the
     *  re-enumerated, openable device after the BROM->loader switch. The device
     *  drops USB, reboots the loader, and re-appears (same 29F1:0230) — which
     *  can take several seconds. We try to open each candidate (a stale BROM
     *  handle won't open), up to `attempts` times. The `connect` event catches
     *  cases where getDevices() lags. Returns an opened+claimed device, or null. */
    async _waitForReenumeratedDevice(attempts = 3, delayMs = 600) {
      // Capture the next matching `connect` event, if any.
      let connectedDev = null;
      const onConnect = (e) => {
        const d = e.device;
        if (d && d.vendorId === K230_VID && d.productId === K230_PID) connectedDev = d;
      };
      try {
        navigator.usb.addEventListener("connect", onConnect);
      } catch (_) {}

      try {
        for (let i = 0; i < attempts; i++) {
          await sleep(delayMs);
          let dev = connectedDev;
          if (!dev) {
            try {
              const devs = await navigator.usb.getDevices();
              dev = devs.find((d) => d.vendorId === K230_VID && d.productId === K230_PID) || null;
            } catch (e) {
              dev = null;
            }
          }
          if (!dev) {
            continue; // not granted to us — expected; we fall back to re-pick
          }
          // Try to open + claim it. A stale/closing handle throws; the fresh
          // loader-mode instance opens cleanly.
          try {
            this.device = dev;
            await this._open();
            return dev;
          } catch (e) {
            try { await dev.close(); } catch (_) {}
            this.device = null;
            connectedDev = null; // re-poll on next loop
          }
        }
      } finally {
        try { navigator.usb.removeEventListener("connect", onConnect); } catch (_) {}
      }
      return null;
    }

    /**
     * transferIn with a timeout. WebUSB's transferIn has NO native timeout and
     * blocks forever if the device sends nothing — which hangs us (e.g. the NOP
     * drain read on a fresh handle, or a command that gets no reply). The Python
     * reference uses a 1s libusb timeout; we race the read against a timer.
     * On timeout we throw a tagged error (the caller decides if that's fatal).
     * Note: the underlying USB read isn't truly cancelled, but the device
     * delivers at most one stale packet later, which the next NOP drains.
     */
    _transferInTimeout(len, timeoutMs) {
      const read = this.device.transferIn(this.epIn, len);
      let timer;
      const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => {
          const err = new Error("transferIn timeout");
          err._timeout = true;
          reject(err);
        }, timeoutMs);
      });
      return Promise.race([read, timeout]).finally(() => clearTimeout(timer));
    }

    async _open() {
      const dev = this.device;
      // ALWAYS rediscover from THIS device's own config — never carry over the
      // previous (BROM) device's endpoints. BROM uses OUT 0x01, the loader uses
      // OUT 0x02; reusing the old number sends probe into a black hole and hangs.
      this.epIn = null;
      this.epOut = null;
      this.iface = null;

      await dev.open();
      if (dev.configuration === null) {
        await dev.selectConfiguration(1);
      }
      // Find the interface that has bulk IN+OUT endpoints and claim it. Port of
      // burners._discover_endpoints(): walk the active config, take the bulk IN
      // (addr & 0x80) and bulk OUT from whichever interface exposes them.
      const cfg = dev.configuration;
      let claimed = false;
      for (const iface of cfg.interfaces) {
        const alt = iface.alternate;
        const inEp = alt.endpoints.find((e) => e.type === "bulk" && e.direction === "in");
        const outEp = alt.endpoints.find((e) => e.type === "bulk" && e.direction === "out");
        if (!inEp || !outEp) continue; // need BOTH on the same interface
        try {
          await dev.claimInterface(iface.interfaceNumber);
        } catch (e) {
          // On Windows a wrong/no WinUSB driver bind surfaces here.
          throw new Error(
            "Could not claim the USB interface. On Windows the device needs the " +
              "WinUSB driver bound to it (use Zadig once — note the loader 'USB " +
              "download gadget' may need its own bind). (" + e.message + ")"
          );
        }
        this.iface = iface.interfaceNumber;
        this.epIn = inEp.endpointNumber;
        this.epOut = outEp.endpointNumber;
        // WebUSB transferIn requires the requested length to be a multiple of
        // the endpoint's max packet size (e.g. 512). Reading 60 hangs/errors on
        // Chrome even though the device replies. Read a full packet, slice later.
        this.epInPacketSize = inEp.packetSize || 512;
        claimed = true;
        break;
      }
      if (!claimed) throw new Error("No interface with both bulk IN+OUT endpoints found");
      if (this.epIn == null || this.epOut == null) {
        throw new Error("Could not locate bulk IN/OUT endpoints");
      }
      this.log(
        "USB ready — claimed interface " + this.iface +
          ", interfaces=" + cfg.interfaces.length +
          ", epIn=#" + this.epIn + " epOut=#" + this.epOut
      );
      if (this._debug) {
        // Dump the full interface/endpoint map of the loader device so we can
        // see exactly what it exposes (helps when loader != BROM layout).
        for (const iface of cfg.interfaces) {
          const a = iface.alternate;
          const eps = a.endpoints
            .map((e) => e.direction + "/" + e.type + "/n" + e.endpointNumber + "/mp" + e.packetSize)
            .join(", ");
          this.log("  iface " + iface.interfaceNumber + " class=" + a.interfaceClass + " eps[" + eps + "]");
        }
      }
    }

    async _close() {
      if (!this.device) return;
      try {
        await this.device.releaseInterface(this.iface);
      } catch (e) {
        /* ignore */
      }
      try {
        await this.device.close();
      } catch (e) {
        /* ignore */
      }
    }

    // ---- Mode detection (EP0 control IN, usb_utils.probe_device) ----------

    async detectMode() {
      try {
        const res = await this.device.controlTransferIn(
          {
            requestType: "vendor",
            recipient: "device",
            request: EP0_GET_CPU_INFO,
            value: 0,
            index: 0,
          },
          32
        );
        if (res.status !== "ok" || !res.data) return DEV_INVALID;
        const bytes = new Uint8Array(res.data.buffer);
        let str = "";
        for (let i = 0; i < bytes.length; i++) {
          if (bytes[i] === 0) break;
          str += String.fromCharCode(bytes[i]);
        }
        this.log("device CPU info: " + JSON.stringify(str.trim()));
        if (str.indexOf("Uboot Stage") >= 0) return DEV_UBOOT;
        if (str.indexOf("K230") >= 0) return DEV_BROM;
        return DEV_INVALID;
      } catch (e) {
        this.log("detectMode failed: " + e.message);
        return DEV_INVALID;
      }
    }

    // ---- BROM -> loader (K230BROMBurner) -----------------------------------

    async _setDataAddress(address) {
      const addrHigh = (address >>> 16) & 0xffff;
      const addrLow = address & 0xffff;
      const res = await this.device.controlTransferOut({
        requestType: "vendor",
        recipient: "device",
        request: EP0_SET_DATA_ADDRESS,
        value: addrHigh,
        index: addrLow,
      });
      if (res.status !== "ok") throw new Error("set_data_address failed");
    }

    async _bootFrom(address) {
      const addrHigh = (address >>> 16) & 0xffff;
      const addrLow = address & 0xffff;
      const res = await this.device.controlTransferOut({
        requestType: "vendor",
        recipient: "device",
        request: EP0_PROG_START,
        value: addrHigh,
        index: addrLow,
      });
      if (res.status !== "ok") throw new Error("boot_from failed");
    }

    /** Upload the SPI-NAND loader to LOADER_ADDRESS and boot it.
     *  Port of K230BROMBurner.write() + boot_from(): set address, stream the
     *  loader in 1000-byte pages over bulk OUT, then PROG_START. */
    async uploadLoader(loaderBytes) {
      this.log("uploading SPI-NAND loader (" + loaderBytes.length + " bytes)...");
      await this._setDataAddress(LOADER_ADDRESS);
      const total = loaderBytes.length;
      for (let off = 0; off < total; off += BROM_PAGE_SIZE) {
        const chunk = loaderBytes.subarray(off, Math.min(off + BROM_PAGE_SIZE, total));
        const res = await this.device.transferOut(this.epOut, chunk);
        if (res.status !== "ok" || res.bytesWritten !== chunk.length) {
          throw new Error("loader chunk write failed");
        }
        this.onProgress({
          phase: "loader",
          current: Math.min(off + chunk.length, total),
          total,
        });
      }
      await this._bootFrom(LOADER_ADDRESS);
      this.log("loader booted; waiting for re-enumeration...");
    }

    // ---- KBURN command framing (burners.send_cmd) --------------------------

    /**
     * Send a 60-byte KBURN packet and (optionally) read+validate the 60-byte
     * reply. Returns the response payload (Uint8Array of expectedRespLen) or
     * null when expectedRespLen === 0. respTimeoutMs bounds the reply wait.
     */
    async sendCmd(cmd, payload, expectedRespLen, respTimeoutMs = 8000) {
      payload = payload || new Uint8Array(0);
      if (payload.length > MAX_DATA_SIZE) {
        throw new Error("command payload too large: " + payload.length);
      }
      const packet = new Uint8Array(PACKET_SIZE);
      const dv = new DataView(packet.buffer);
      dv.setUint16(0, cmd, true);
      dv.setUint16(2, 0, true); // result = 0 in request
      dv.setUint16(4, payload.length, true);
      packet.set(payload, HEADER_SIZE);

      const wr = await this.device.transferOut(this.epOut, packet);
      if (wr.status !== "ok") throw new Error("command write failed (cmd 0x" + cmd.toString(16) + ", status " + wr.status + ")");
      if (this._debug) this.log("  cmd 0x" + cmd.toString(16) + " sent (" + wr.bytesWritten + "/" + PACKET_SIZE + " bytes), awaiting reply…");

      if (expectedRespLen === 0) return null;

      // Read the reply with a timeout so a non-replying command errors instead
      // of hanging forever (WebUSB transferIn has no native timeout). respTimeoutMs
      // is generous — the 56 MiB data-partition erase can take ~5s before its ACK.
      // NOTE: request a FULL max-packet (e.g. 512), not 60 — Chrome's WebUSB needs
      // the length to be a multiple of the endpoint packet size or it hangs.
      const rd = await this._transferInTimeout(this.epInPacketSize, respTimeoutMs);
      if (rd.status !== "ok" || !rd.data || rd.data.byteLength < HEADER_SIZE) {
        throw new Error("command response read failed (cmd 0x" + cmd.toString(16) + ")");
      }
      const resp = new Uint8Array(rd.data.buffer);
      const rdv = new DataView(resp.buffer, resp.byteOffset, resp.byteLength);
      const respCmd = rdv.getUint16(0, true);
      const respResult = rdv.getUint16(2, true);
      const respSize = rdv.getUint16(4, true);

      // NOP (CMD_NONE) just clears error state. The device replies with an error
      // ("NOT SUPPORT FUNC" in the vendor log) — different cmd echo, non-OK result,
      // and possibly a different data_size. The stock tool logs it and moves on.
      // So for NOP we accept WHATEVER comes back and don't validate anything.
      if (cmd === CMD_NONE) {
        return resp.subarray(HEADER_SIZE, Math.min(HEADER_SIZE + respSize, resp.length));
      }

      if (respCmd !== (cmd | CMD_FLAG_DEV_TO_HOST)) {
        throw new Error(
          "response cmd mismatch: got 0x" +
            respCmd.toString(16) +
            ", expected 0x" +
            (cmd | CMD_FLAG_DEV_TO_HOST).toString(16)
        );
      }
      if (respResult !== KBURN_RESULT_OK) {
        throw new Error("device error result 0x" + respResult.toString(16) + " (cmd 0x" + cmd.toString(16) + ")");
      }
      if (respSize !== expectedRespLen) {
        throw new Error("response size mismatch: expected " + expectedRespLen + ", got " + respSize);
      }
      return resp.subarray(HEADER_SIZE, HEADER_SIZE + respSize);
    }

    /** NOP — clear device error state.
     *  IMPORTANT: do NOT do a speculative "drain" transferIn here. In WebUSB a
     *  timed-out transferIn is NOT actually cancelled — it stays queued on the
     *  endpoint. A second transferIn (for the real reply) then sits behind it,
     *  and the device's reply gets delivered to the abandoned drain read, so the
     *  real read hangs forever. (libusb/pyusb can do the drain because its read
     *  timeout truly cancels — WebUSB can't.) So just send the NOP and read once. */
    async nop() {
      await this.sendCmd(CMD_NONE, new Uint8Array(0), 16);
    }

    /** probe() — set chunk sizes from the device's 16-byte reply. */
    async probe() {
      await this.nop();
      const payload = new Uint8Array([MEDIUM_SPI_NAND, 0xff]);
      const resp = await this.sendCmd(CMD_DEV_PROBE, payload, 16);
      const dv = new DataView(resp.buffer, resp.byteOffset, resp.byteLength);
      // <QQ> = (out_chunk_size, in_chunk_size)
      const outLo = dv.getUint32(0, true);
      const outHi = dv.getUint32(4, true);
      const inLo = dv.getUint32(8, true);
      const inHi = dv.getUint32(12, true);
      this.outChunkSize = outHi * 0x100000000 + outLo;
      this.inChunkSize = inHi * 0x100000000 + inLo;
      if (!this.outChunkSize) throw new Error("probe returned zero out_chunk_size");
      this.log("probe: out_chunk_size=" + this.outChunkSize + " in_chunk_size=" + this.inChunkSize);
    }

    /** get_capacity() — 32-byte <QQQQ> (capacity, blk_sz, erase_size, bitfields). */
    async getCapacity() {
      const resp = await this.sendCmd(CMD_DEV_GET_INFO, new Uint8Array(0), 32);
      const dv = new DataView(resp.buffer, resp.byteOffset, resp.byteLength);
      const capLo = dv.getUint32(0, true);
      const capHi = dv.getUint32(4, true);
      const blkLo = dv.getUint32(8, true);
      const eraseLo = dv.getUint32(16, true);
      this.capacity = capHi * 0x100000000 + capLo;
      this.blkSz = blkLo || 512;
      const eraseSize = eraseLo;
      this.log(
        "device: capacity " +
          Math.round(this.capacity / (1024 * 1024)) +
          " MB, blk_sz " +
          this.blkSz +
          ", erase_size " +
          eraseSize
      );
      return this.capacity;
    }

    /** Full-range erase of (offset, size). NOP first. */
    async erase(offset, size) {
      await this.nop();
      const payload = new Uint8Array(16);
      payload.set(u64le(offset), 0);
      payload.set(u64le(size), 8);
      // Erase returns an ACK packet; burners.erase_lba waits for one read.
      // The 56 MiB data partition can take ~5s before its ACK — give it 60s.
      await this.sendCmd(CMD_ERASE_LBA, payload, 16, 60000);
    }

    /** write_start (0x21): <QQQQ> = (offset, size, size, flags=0), expect 8. */
    async _writeStart(offset, size) {
      if (offset % this.blkSz !== 0) {
        throw new Error("write offset 0x" + offset.toString(16) + " not aligned to blk_sz " + this.blkSz);
      }
      await this.nop();
      const payload = new Uint8Array(32);
      payload.set(u64le(offset), 0);
      payload.set(u64le(size), 8);
      payload.set(u64le(size), 16);
      payload.set(u64le(0), 24); // flags
      await this.sendCmd(CMD_WRITE_LBA, payload, 8);
    }

    /** Stream partition bytes as raw bulk writes in out_chunk_size chunks,
     *  with a terminating ZLP when the total is a chunk-size multiple. */
    async _writeChunks(data, onChunk) {
      const total = data.length;
      const cs = this.outChunkSize;
      let sent = 0;
      for (let i = 0; i < total; i += cs) {
        const chunk = data.subarray(i, Math.min(i + cs, total));
        const res = await this.device.transferOut(this.epOut, chunk);
        if (res.status !== "ok") throw new Error("data chunk write failed");
        sent += chunk.length;
        if (onChunk) onChunk(sent, total);
      }
      if (total % cs === 0) {
        await this.device.transferOut(this.epOut, new Uint8Array(0)); // ZLP
      }
    }

    async writeImage(data, offset, onChunk) {
      await this._writeStart(offset, data.length);
      await this._writeChunks(data, onChunk);
    }

    /** reboot (0x01): NOP, then <Q> = REBOOT_MARK, no response. Device drops USB. */
    async reboot() {
      try {
        await this.nop();
      } catch (e) {
        /* ignore */
      }
      try {
        await this.sendCmd(CMD_REBOOT, u64le(REBOOT_MARK), 0);
      } catch (e) {
        // expected — the device may reboot before ACKing
        this.log("reboot command sent (device dropped USB — expected)");
      }
      await sleep(2000);
    }

    // ---- Orchestration -----------------------------------------------------

    /**
     * READINESS CHECK — prove the hardware is present, claimable, and a real
     * K230 in BOOT mode, WITHOUT downloading firmware or touching the paid
     * token. Call this first; only proceed to flashImage() if it resolves.
     * Leaves the device open + claimed and records the detected mode.
     * Throws a user-friendly Error if anything isn't ready.
     */
    async connectAndVerify() {
      if (!this.device) throw new Error("no device selected");
      await this._open(); // claims interface; throws WinUSB hint on Windows
      const mode = await this.detectMode();
      if (mode === DEV_INVALID) {
        throw new Error(
          "Device is connected but not responding as a K230 in BOOT mode. " +
            "Power it off, hold the recovery button, replug the USB cable, then release after ~2s."
        );
      }
      this._verifiedMode = mode;
      this.log("device ready — mode: " + (mode === DEV_BROM ? "BROM" : "loader"));
      return mode;
    }

    /**
     * Flash a parsed kdimg over the device that connectAndVerify() already
     * opened + verified. Does loader-upload (if BROM) -> probe -> erase ->
     * write -> reboot. Burn the token (download the kdimg) only after
     * connectAndVerify() has succeeded.
     * @param {ArrayBuffer} kdimgBuffer  the raw .kdimg bytes
     * @param {Uint8Array}  loaderBytes  loader_spi_nand.bin
     * @param {Object}      opts
     * @param {Function}    opts.repick  async () => USBDevice — called when the
     *        loader-mode device can't be auto-reacquired (WebUSB only returns
     *        devices the page was granted; the re-enumerated loader instance is
     *        a NEW device needing its own permission). Must run from a user
     *        gesture (a button) and return the device from navigator.usb.requestDevice().
     */
    async flashImage(kdimgBuffer, loaderBytes, opts = {}) {
      const KP = global.KDImgParser;
      if (!KP) throw new Error("kdimg-parser.js not loaded");
      if (!this.device) throw new Error("device not connected — run connectAndVerify() first");

      // Parse + verify the image (per-partition SHA-256 happens at write time).
      this.log("parsing kdimg...");
      const { header, partitions } = KP.parseKdimg(kdimgBuffer);
      this.log(
        "kdimg v" + header.version + ", " + header.partTblNum + " partitions (" +
          header.imageInfo + " / " + header.boardInfo + ")"
      );

      let mode = this._verifiedMode != null ? this._verifiedMode : await this.detectMode();
      if (mode === DEV_BROM) {
        this.log("device in BROM mode — uploading loader");
        await this.uploadLoader(loaderBytes);
        // Re-enumerate: after boot_from, the BROM handle is dead. The device
        // drops USB, reboots into the loader, and re-appears (same 29F1:0230).
        // Close our stale handle, then try to auto-reacquire it.
        try { await this._close(); } catch (_) {}
        this.device = null;
        this.log("miner is switching into flashing mode…");
        // Quick auto-reacquire attempt (a couple of tries). It almost never
        // succeeds — the loader is a NEW USB device the page wasn't granted —
        // so we go straight to the re-pick rather than counting down.
        let redev = await this._waitForReenumeratedDevice();

        // Re-pick: the loader-mode device is a new USB instance the page wasn't
        // granted, so WebUSB can't auto-return it. requestDevice() needs a user
        // gesture, so the page surfaces a quick prompt and we resume here.
        if (!redev && typeof opts.repick === "function") {
          this.log("reconnect: select the miner again to continue…");
          let picked = null;
          try {
            picked = await opts.repick();
          } catch (e) {
            picked = null;
          }
          if (picked) {
            this.device = picked;
            try {
              await this._open();
              redev = picked;
            } catch (e) {
              this.device = null;
              throw new Error("Selected the device but couldn't open it: " + (e.message || e));
            }
          }
        }

        if (!redev) {
          throw new Error(
            "Device didn't come back in loader mode after the loader upload. " +
              "When prompted, pick the device again (it re-appears with a new USB " +
              "identity). On Windows, also bind WinUSB to the loader-mode device in Zadig if needed."
          );
        }
        // device is now open + claimed (either auto-reacquired or re-picked).
        mode = await this.detectMode();
        if (mode !== DEV_UBOOT) {
          throw new Error(
            "Device re-appeared but isn't in loader mode (got mode " + mode + "). " +
              "Re-enter BOOT mode and try again."
          );
        }
        this.log("device is now in loader mode");
        // Give the loader a moment to be ready for KBURN commands after the
        // mode switch (the detectMode control-IN can race the loader coming up).
        await sleep(500);
      } else if (mode !== DEV_UBOOT) {
        throw new Error("unexpected device mode: " + mode + " (is it in BOOT mode?)");
      }

      this.log("probing NAND...");
      await this.probe();
      await this.getCapacity();

      // --- Erase phase: full-range erase every UBI partition (erase_size>0). ---
      const ubiParts = partitions.filter((p) => p.eraseSize > 0);
      // Fallback: if no parsed partition covers the persistent data region,
      // erase it explicitly (older kdimgs omit data_ubi — see HANDOFF §B).
      const dataCovered = ubiParts.some(
        (p) =>
          p.nandOffset <= DATA_PART_OFFSET &&
          p.nandOffset + p.eraseSize >= DATA_PART_OFFSET + DATA_PART_ERASE
      );
      this.log("erasing " + ubiParts.length + " UBI partition(s) (full range)...");
      let eraseIdx = 0;
      const eraseTotal = ubiParts.length + (dataCovered ? 0 : 1);
      for (const p of ubiParts) {
        this.onProgress({ phase: "erase", partName: p.name, partsDone: eraseIdx, partsTotal: eraseTotal });
        this.log("  erase " + p.name + " @0x" + p.nandOffset.toString(16) + " size 0x" + p.eraseSize.toString(16));
        await this.erase(p.nandOffset, p.eraseSize);
        eraseIdx++;
      }
      if (!dataCovered) {
        this.onProgress({ phase: "erase", partName: "data", partsDone: eraseIdx, partsTotal: eraseTotal });
        this.log("  erase data (fallback) @0x" + DATA_PART_OFFSET.toString(16));
        await this.erase(DATA_PART_OFFSET, DATA_PART_ERASE);
      }

      // --- Write phase: each partition with content, padded + SHA-256 verified. ---
      const writeParts = partitions.filter((p) => p.contentSize > 0);
      this.log("writing " + writeParts.length + " partition(s)...");
      let wi = 0;
      for (const p of writeParts) {
        this.onProgress({
          phase: "write",
          partName: p.name,
          partsDone: wi,
          partsTotal: writeParts.length,
          current: 0,
          total: p.partSize,
        });
        this.log("  write " + p.name + " @0x" + p.nandOffset.toString(16) + " (" + p.partSize + " bytes)");
        // readPartData verifies SHA-256 and pads to partSize.
        const data = await KP.readPartData(kdimgBuffer, p);
        await this.writeImage(data, p.nandOffset, (cur, tot) => {
          this.onProgress({
            phase: "write",
            partName: p.name,
            partsDone: wi,
            partsTotal: writeParts.length,
            current: cur,
            total: tot,
          });
        });
        wi++;
      }

      this.log("rebooting device...");
      await this.reboot();
      await this._close();
      this.log("flash complete — device is rebooting into TNA-OS");
      return true;
    }
  }

  global.K230Flasher = K230Flasher;
})(typeof window !== "undefined" ? window : globalThis);

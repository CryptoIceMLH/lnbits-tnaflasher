/*
 * kdimg-parser.js — parses a Canaan K230 .kdimg whole-device image in the browser.
 *
 * Direct port of k230_flash/kdimage.py (the firmware side's source of truth).
 * Handles both V1 and V2 partition-table formats, verifies the header CRC32,
 * the partition-table CRC32, and each partition's SHA-256 — exactly like the
 * Python parser. Used by the WebUSB flasher (k230-flasher.js).
 *
 * Layout:
 *   offset 0x000  512-byte header  "<6I 32s 32s 64s"
 *                 (magic, crc32, flag, version, part_tbl_num, part_tbl_crc32,
 *                  image_info[32], chip_info[32], board_info[64])
 *   offset 0x200  partition table: part_tbl_num entries x 256 bytes
 *   ...           concatenated partition payloads
 */

const KDIMG_HEADER_MAGIC = 0x27cb8f93;
const KDIMG_PART_MAGIC = 0x91df6da4;
const KDIMG_HEADER_SIZE = 512;
const PART_STRUCT_SIZE = 256;

// CRC32 (IEEE 802.3, same polynomial Python's zlib.crc32 uses). Table-driven.
const CRC32_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(bytes, start = 0, end = bytes.length) {
  let crc = 0xffffffff;
  for (let i = start; i < end; i++) {
    crc = CRC32_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

// Read a little-endian uint64 as a JS Number. Partition offsets/sizes here are
// well under 2^53, so Number is safe and avoids BigInt friction downstream.
function readU64(view, off) {
  const lo = view.getUint32(off, true);
  const hi = view.getUint32(off + 4, true);
  return hi * 0x100000000 + lo;
}

function decodeAscii(bytes) {
  let end = bytes.length;
  for (let i = 0; i < bytes.length; i++) {
    if (bytes[i] === 0) {
      end = i;
      break;
    }
  }
  let s = "";
  for (let i = 0; i < end; i++) s += String.fromCharCode(bytes[i]);
  return s;
}

function toHex(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i++) {
    s += bytes[i].toString(16).padStart(2, "0");
  }
  return s;
}

/**
 * Parse a .kdimg ArrayBuffer into a header + ordered partition list.
 * Throws Error on any structural / CRC failure.
 *
 * Returns:
 *   {
 *     header: { magic, version, partTblNum, imageInfo, chipInfo, boardInfo },
 *     partitions: [{
 *       name, nandOffset, partSize, eraseSize, maxSize,
 *       contentOffset, contentSize, sha256 (hex), flag
 *     }]  // sorted by nandOffset, matching kdimage.py sort()
 *   }
 */
function parseKdimg(arrayBuffer) {
  const bytes = new Uint8Array(arrayBuffer);
  if (bytes.length < KDIMG_HEADER_SIZE) {
    throw new Error("kdimg too small to contain a header");
  }
  const view = new DataView(arrayBuffer);

  // ---- Header ----
  const magic = view.getUint32(0, true);
  if (magic !== KDIMG_HEADER_MAGIC) {
    throw new Error(
      `bad kdimg header magic: got 0x${magic.toString(16)}, expected 0x27cb8f93`
    );
  }
  const hdrCrc32 = view.getUint32(4, true);
  const flag = view.getUint32(8, true);
  const version = view.getUint32(12, true);
  const partTblNum = view.getUint32(16, true);
  const partTblCrc32 = view.getUint32(20, true);
  const imageInfo = decodeAscii(bytes.subarray(24, 56));
  const chipInfo = decodeAscii(bytes.subarray(56, 88));
  const boardInfo = decodeAscii(bytes.subarray(88, 152));

  // Header CRC32: zero out the crc32 field (bytes [4:8]) then crc the full 512.
  const hdrCopy = bytes.slice(0, KDIMG_HEADER_SIZE);
  hdrCopy[4] = hdrCopy[5] = hdrCopy[6] = hdrCopy[7] = 0;
  const calcHdrCrc = crc32(hdrCopy);
  if (calcHdrCrc !== hdrCrc32) {
    throw new Error(
      `header CRC32 mismatch: got 0x${calcHdrCrc.toString(16)}, expected 0x${hdrCrc32.toString(16)}`
    );
  }

  // ---- Partition table ----
  const tableSize = partTblNum * PART_STRUCT_SIZE;
  const tableStart = KDIMG_HEADER_SIZE;
  const tableEnd = tableStart + tableSize;
  if (tableEnd > bytes.length) {
    throw new Error("kdimg truncated: partition table extends past end of file");
  }
  const calcTblCrc = crc32(bytes, tableStart, tableEnd);
  if (calcTblCrc !== partTblCrc32) {
    throw new Error(
      `partition-table CRC32 mismatch: got 0x${calcTblCrc.toString(16)}, expected 0x${partTblCrc32.toString(16)}`
    );
  }

  const partitions = [];
  for (let i = 0; i < partTblNum; i++) {
    const base = tableStart + i * PART_STRUCT_SIZE;
    const partMagic = view.getUint32(base + 0x00, true);
    if (partMagic !== KDIMG_PART_MAGIC) {
      throw new Error(`bad partition magic at entry ${i}`);
    }

    // Common leading fields (identical in V1 and V2):
    //   +0x00 magic, +0x04 offset, +0x08 size, +0x0C erase_size, +0x10 max_size
    const nandOffset = view.getUint32(base + 0x04, true);
    const partSize = view.getUint32(base + 0x08, true);
    const eraseSize = view.getUint32(base + 0x0c, true);
    const maxSize = view.getUint32(base + 0x10, true);

    let flag, contentOffset, contentSize, shaOff;
    if (version >= 2) {
      // V2  "<5I 4x Q I I 32s 32s": after 5 u32 (0x00..0x14) there are 4 pad
      // bytes, then a u64 flag (0x18), then content_offset (0x20) + size (0x24),
      // then sha256[32] at 0x28, name[32] at 0x48.
      flag = readU64(view, base + 0x18);
      contentOffset = view.getUint32(base + 0x20, true);
      contentSize = view.getUint32(base + 0x24, true);
      shaOff = base + 0x28;
    } else {
      // V1  "<8I 32s 32s": flag is a u32 at 0x14, content_offset 0x18,
      // content_size 0x1C, sha256[32] at 0x20, name[32] at 0x40.
      flag = view.getUint32(base + 0x14, true);
      contentOffset = view.getUint32(base + 0x18, true);
      contentSize = view.getUint32(base + 0x1c, true);
      shaOff = base + 0x20;
    }
    const sha256 = toHex(bytes.subarray(shaOff, shaOff + 32));
    const name = decodeAscii(bytes.subarray(shaOff + 32, shaOff + 64));

    partitions.push({
      name,
      nandOffset,
      partSize,
      eraseSize,
      maxSize,
      contentOffset,
      contentSize,
      sha256,
      flag,
    });
  }

  // kdimage.py sorts items by partOffset before writing.
  partitions.sort((a, b) => a.nandOffset - b.nandOffset);

  return {
    header: { magic, version, partTblNum, imageInfo, chipInfo, boardInfo },
    partitions,
  };
}

/**
 * Read a partition's payload from the kdimg, verify its SHA-256, and pad with
 * 0xFF up to partSize — the byte stream that gets written to NAND.
 * Mirrors kdimage.py read_part_data(). `subtleCrypto` is optional (defaults to
 * window.crypto.subtle); pass null to skip the verify (not recommended).
 */
async function readPartData(arrayBuffer, part, subtleCrypto = (typeof crypto !== "undefined" ? crypto.subtle : null)) {
  const bytes = new Uint8Array(arrayBuffer);
  const start = part.contentOffset;
  const end = start + part.contentSize;
  if (end > bytes.length) {
    throw new Error(`partition ${part.name}: content extends past end of file`);
  }
  const content = bytes.subarray(start, end);

  if (subtleCrypto) {
    const digest = await subtleCrypto.digest("SHA-256", content);
    const got = toHex(new Uint8Array(digest));
    if (got !== part.sha256) {
      throw new Error(
        `partition ${part.name}: SHA-256 mismatch (got ${got}, expected ${part.sha256})`
      );
    }
  }

  if (part.contentSize < part.partSize) {
    const out = new Uint8Array(part.partSize);
    out.set(content, 0);
    out.fill(0xff, part.contentSize);
    return out;
  }
  // copy out a standalone array (subarray is a view into the whole buffer)
  return content.slice();
}

// Export for both browser (global) and Node/CommonJS (tests).
const KDImgParser = { parseKdimg, readPartData, crc32, KDIMG_HEADER_MAGIC, KDIMG_PART_MAGIC };
if (typeof module !== "undefined" && module.exports) {
  module.exports = KDImgParser;
}
if (typeof window !== "undefined") {
  window.KDImgParser = KDImgParser;
} else if (typeof globalThis !== "undefined") {
  globalThis.KDImgParser = KDImgParser;
}

# Blurb BookSmart `.bev` File Format

## Summary

`.bev` files in BookSmart's theme library are **not archives**. Each file is a
**single image encrypted with DES**. The `*_frame_*` assets decrypt to SVG vector
graphics; the larger `*_pat_*` (pattern) assets are raster images (PNG/JPG)
encrypted the same way.

## Encryption details

| Property    | Value                                                         |
|-------------|---------------------------------------------------------------|
| Algorithm   | DES                                                           |
| Mode        | ECB                                                           |
| Padding     | PKCS5                                                         |
| Java cipher | `Cipher.getInstance("DES")` (defaults to `DES/ECB/PKCS5Padding`) |
| Key         | ASCII string `blurbboo` (hex `626c757262626f6f`)             |

The key comes from `new DESKeySpec("blurbbooks".getBytes())` — `DESKeySpec`
uses only the first 8 bytes of `"blurbbooks"`, i.e. `blurbboo`.

## Where the code lives

- `lib/booksmart-core-1.0.jar`
  - `com/blurb/booksmart/util/ImageUtil` — reads `.BEV` files through a
    `javax.crypto.CipherInputStream`, then hands the plaintext to `ImageIO`
    (or `readSVG` for vector assets).
  - `com/blurb/booksmart/util/Misc` — `getContentDecipher()` returns the
    `decipher` cipher; the DES key/cipher is built in the static initializer
    (`Misc.<clinit>`).

## How it was identified

1. Entropy ~7.96 bits/byte with all 256 byte values present and no magic
   header → encrypted/compressed content.
2. All `.bev` files share an identical 72-byte prefix, and every file size is a
   multiple of 8 → block cipher in ECB mode with an 8-byte block (DES),
   encrypting a constant image header.
3. `ImageUtil` reads `.BEV` via `CipherInputStream` using
   `Misc.getContentDecipher()`.
4. Disassembling `Misc.<clinit>` revealed the DES setup and the `blurbbooks`
   key string.
5. Decryption produced a valid `<?xml ... <svg>` document (an Adobe
   Illustrator-exported decorative frame).

## How to decrypt

### OpenSSL (Fedora 43+ needs the legacy provider; DES is disabled by default)

```bash
openssl enc -d -des-ecb -K 626c757262626f6f \
  -provider legacy -provider default \
  -in elegant_frame_01_02.bev -out elegant_frame_01_02.svg
```

### Java (mirrors the app exactly)

```java
import javax.crypto.*;
import javax.crypto.spec.*;
import java.nio.file.*;

public class Dec {
  public static void main(String[] a) throws Exception {
    SecretKey k = SecretKeyFactory.getInstance("DES")
        .generateSecret(new DESKeySpec("blurbbooks".getBytes()));
    Cipher c = Cipher.getInstance("DES");
    c.init(Cipher.DECRYPT_MODE, k);
    byte[] in = Files.readAllBytes(Paths.get(a[0]));
    Files.write(Paths.get(a[1]), c.doFinal(in));
  }
}
```

```bash
javac Dec.java
java Dec elegant_frame_01_02.bev elegant_frame_01_02.svg
```

After decryption, run `file` on the output to confirm the real type
(SVG / PNG / JPEG) and rename with the appropriate extension.

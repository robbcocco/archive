# printer ICC profiles

Drop a printer/paper `.icc` or `.icm` profile in this directory (first one
alphabetically wins) and the web UI soft-proofs its previews through it —
the thumbnail then approximates how the print will actually look.

Profiles are gitignored; only this README is tracked.

## Getting the Canon profile without Windows

Canon bundles per-paper ICM profiles inside the Windows driver package:

```sh
# download "G600 series MP Drivers" (.exe) from Canon's support site, then:
7z x g600-driver.exe -oextracted
find extracted -iname '*.icm'
# look for the Glossy II one (name contains GL or PP), copy it here:
cp extracted/.../CNB*PP*.icm ./
docker compose up -d --build webui
```

`7z` = p7zip-full package. The exe is a plain archive; no Windows needed.

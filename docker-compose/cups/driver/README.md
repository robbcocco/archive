# Canon cnijfilter2 driver (optional)

Drop Canon's Linux driver `.deb` here and rebuild; the image installs it
and the entrypoint creates a `photo-canon` queue rendering through Canon's
own color pipeline (the "vendor look" the driverless queues can't produce).

```sh
# from Canon's G600 series support page, download
# "IJ Printer Driver for Linux (debian packages)" — cnijfilter2-*-deb.tar.gz
tar xf cnijfilter2-*-deb.tar.gz
cp cnijfilter2-*/packages/cnijfilter2_*_amd64.deb ./
docker compose build cups && docker compose up -d cups
```

Without a deb this directory is inert — the build skips installation and
the queue simply isn't created.

Notes:
- amd64 only (matches the server)
- queue transport defaults to `PRINTER_URI`; if the printer rejects the
  Canon data stream over IPP, set `CANON_PRINTER_URI` in `.env` to an
  `lpd://<printer-ip>/lp` or `socket://<printer-ip>:9100` URI
- pick paper size/type per job in the web UI — the Canon PPD exposes its
  own media keywords, listed automatically when the queue is selected

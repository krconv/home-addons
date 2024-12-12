OpenPrinting CUPS v2.4.12
=========================

![Version](https://img.shields.io/github/v/release/openprinting/cups?include_prereleases)
![Apache 2.0](https://img.shields.io/github/license/openprinting/cups)
[![Build and Test](https://github.com/OpenPrinting/cups/workflows/Build%20and%20Test/badge.svg)](https://github.com/OpenPrinting/cups/actions/workflows/build.yml)
[![Coverity Scan](https://img.shields.io/coverity/scan/23806)](https://scan.coverity.com/projects/openprinting-cups)


Introduction
------------

OpenPrinting CUPS is the most current version of CUPS, a standards-based, open
source printing system for Linux® and other Unix®-like operating systems.  CUPS
supports printing to:

- [AirPrint™][1] and [IPP Everywhere™][2] printers,
- Network and local (USB) printers with Printer Applications, and
- Network and local (USB) printers with (legacy) PPD-based printer drivers.

CUPS provides the System V ("lp") and Berkeley ("lpr") command-line interfaces,
a configurable web interface, a C API, and common print filters, drivers, and
backends for printing.  The [cups-filters][3] project provides additional
filters and drivers.

CUPS is licensed under the Apache License Version 2.0 with an exception to allow
linking against GNU GPL2-only software.  See the files `LICENSE` and `NOTICE`
for more information.

> Note: Apple maintains a separate repository for the CUPS that ships with macOS
> and iOS at <https://github.com/apple/cups>.

[1]: https://support.apple.com/en-us/HT201311
[2]: https://www.pwg.org/ipp/everywhere.html
[3]: https://github.com/openprinting/cups-filters
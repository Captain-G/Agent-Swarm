# Third-party components

Baraza ships these front-end components so that it works offline and needs no
Node.js build step. Each remains under its own license.

| Component | Version | Files | License | Source |
|---|---|---|---|---|
| htmx | 2.0.11 | `static/vendor/htmx-2.0.11.min.js` | BSD Zero-Clause (0BSD) | https://github.com/bigskysoftware/htmx |
| Alpine.js | 3.17.4 | `static/vendor/alpine-3.17.4.min.js` | MIT | https://github.com/alpinejs/alpine |
| Chart.js | 4.5.1 | `static/vendor/chart-4.5.1.umd.min.js` | MIT | https://github.com/chartjs/Chart.js |
| D3 | 7.9.0 | `static/vendor/d3-7.9.0.min.js` | ISC | https://github.com/d3/d3 |
| Lucide icons (lucide-static) | 1.52.0 | `static/icons/*.svg` | ISC (portions MIT, from Feather) | https://github.com/lucide-icons/lucide |
| Atkinson Hyperlegible | 2020 | `static/fonts/*.woff2` | SIL Open Font License 1.1 | https://www.brailleinstitute.org/freefont/ (via Fontsource) |

Python dependencies are listed in `requirements.txt` and installed from PyPI
under their own licenses (Django: BSD-3-Clause; httpx: BSD-3-Clause; NetworkX:
BSD-3-Clause; NumPy: BSD-3-Clause; trafilatura: Apache-2.0; Beautiful Soup: MIT;
lxml: BSD-3-Clause; ddgs: MIT; vaderSentiment: MIT; pypdf: BSD-3-Clause;
python-docx: MIT; python-dotenv: BSD-3-Clause; WhiteNoise: MIT).

## License texts

### MIT (Alpine.js, Chart.js, and parts of Lucide)

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN
AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Copyright notices: Alpine.js (c) 2019-2025 Caleb Porzio and contributors;
Chart.js (c) 2014-2024 Chart.js Contributors; Feather (c) 2013-2023 Cole Bemis.

### ISC (D3, Lucide)

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.

Copyright notices: D3 (c) 2010-2023 Mike Bostock; Lucide (c) Lucide Contributors 2022.

### BSD Zero-Clause (htmx)

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.

### SIL Open Font License 1.1 (Atkinson Hyperlegible)

Copyright 2020 Braille Institute of America, Inc. The fonts are licensed under
the SIL Open Font License, Version 1.1 (https://openfontlicense.org). They may
be used, bundled and redistributed freely with software, but may not be sold on
their own; the Reserved Font Name "Atkinson Hyperlegible" applies to modified
versions.

# Vendored browser runtime

`htmx.min.js` and `hx-sse.min.js` are the unmodified htmx 4.0.0-beta6
distribution and native SSE extension from the
[`htmx.org` npm package](https://www.npmjs.com/package/htmx.org). They are
exact-pinned because htmx 4 is still beta.

SHA-256:

```text
28fae7bbe8e8142b702debb9d5234a9a436d9435a4b5165b195aa1a7ed840d25  htmx.min.js
d3aeb71073552b253eaee99badebd60607003b1c25ac567ab88187d6887ab522  hx-sse.min.js
```

It is vendored so the local operator workbench does not require a public CDN.
htmx is distributed under the Zero-Clause BSD license:

> Permission to use, copy, modify, and/or distribute this software for any
> purpose with or without fee is hereby granted.
>
> THE SOFTWARE IS PROVIDED “AS IS” AND THE AUTHOR DISCLAIMS ALL WARRANTIES
> WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
> MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
> SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
> WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION
> OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
> CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

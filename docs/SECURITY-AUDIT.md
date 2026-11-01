# Activity Bub 0.3.2 security review

Scope: hostile public-server API responses, image previews, post rendering, URL
launches, and the local cache. Reviewed against the 0.3.1 source, then hardened
and tested locally on Linux x86-64. This is a source review plus synthetic
regression testing, not an independent penetration test, exploit-completeness
claim, antivirus scan, or certification. No working code-execution exploit was
discovered or exercised during this review.

## Findings and changes

| Area | Finding in 0.3.1 | Behavior in 0.3.2 |
| --- | --- | --- |
| Image decoding | Remote raster containers reached native Qt decoders in the app process. A decoder memory-safety bug could therefore affect the desktop app. PNG metadata cleanup did not address this boundary. | Disposable workers decode after OS restrictions; the GUI accepts only a fixed, bounded raw RGBA packet. |
| Scrubbing | Removing duplicate PNG EXIF chunks was compatibility cleanup, not full metadata stripping or malware detection. | No EXIF/XMP/ICC, original container bytes, extra frames, or file trailers pass from the worker to the GUI. Orientation is applied to pixels in the worker. |
| Network addressing | General public-IP checks did not explicitly cover private IPv4 addresses embedded in IPv6 translation/transition addresses. | Additional checks reject private/reserved embedded IPv4 in mapped, 6to4, Teredo, and the standard NAT64 prefix. Ordinary DNS answers are still checked and connection addresses pinned. |
| Openable URLs | HTTPS validation was looser than crawler hostname validation and truncated long URLs. | Reject controls, backslashes, credentials, IP literals/ambiguous numeric hosts, private/local suffixes, non-443 ports, malformed Unicode, and overlong URLs. Canonicalize the hostname before Qt parses it. |
| Reader and errors | Escaping already kept post HTML out of the renderer, but default text-browser resource behavior remained available. | Resource/document loading and drops are disabled. Controlled markup escapes post text and links. Remote tooltips are escaped and warning dialogs explicitly use plain text. |
| JSON | Input bytes were bounded, but nesting depended on the embedding Python runtime's recursion limit. | Validate UTF-8, enforce nesting limit 64, reject NaN/Infinity, and turn malformed/deep responses into handled fetch errors. |

## Image boundary

1. The HTTPS client verifies TLS, checks DNS answers, pins connections to public
   addresses, revalidates redirects, and caps image payloads at 5 MiB.
2. The parent checks an explicit raster signature. SVG, HTML, TIFF, AVIF/HEIF,
   PDF/EPS, archives, and executables are not preview formats.
3. A fresh worker loads trusted decoder code before it reads any image bytes.
   It removes unrelated environment variables and closes descriptors above 2.
4. On Linux x86-64/arm64 the worker installs no-new-privileges and an
   architecture-checked seccomp syscall allowlist. File opens, sockets,
   fork/clone, exec, ptrace, privilege changes, and descriptor-transfer calls are
   unavailable after setup. Standard input/output remain the bounded IPC channel.
5. Hard limits: 16 million source pixels, 512 MiB address space, 3 CPU seconds,
   no core dumps or regular-file output, and at most two simultaneous decoders.
   The parent caps wall time at 6 seconds and output at 7,680,012 bytes, killing
   hung, oversized, or stopped workers. The memory limit is virtual address space,
   not a statement that the worker normally consumes 512 MiB resident memory.
6. Pillow 13 receives one selected format from PNG/JPEG/GIF/WebP/BMP. Only the
   first frame is used, resized to a bounded preview, with orientation applied.
7. The parent verifies packet magic, dimensions, pixel count, and exact byte
   length before constructing an image from raw RGBA. It does not parse an
   image container, deserialize Python objects, execute commands, or accept paths
   from worker output. A failed policy or decode never triggers a GUI fallback.

The worker can still run native decoder instructions, including a potential
exploit inside that worker. The purpose of the restrictions is to reduce its
ability to reach the rest of the machine. Seccomp alone is not a universal
sandbox; these controls also rely on descriptor closure, fixed IPC, resource
limits, and the kernel. They do not prove freedom from kernel vulnerabilities.

Windows/macOS and unsupported architectures keep remote previews disabled.
Linux arm64 policy constants are included, but hardware validation here was
x86-64. Text feeds and explicit browser links continue to work. There is no
unsafe-preview switch. Trusted local demo artwork does not use remote decoding.

## Existing protections verified

- No app path evaluates post content as Python, shell commands, or templates.
  The worker launch uses fixed argument arrays; remote bytes travel on stdin.
- No embedded web engine or JavaScript is used for posts. Body markup is
  generated locally from escaped text; labels use plain text.
- Database writes and search/filter values use bound SQL parameters. Server
  names are validated before storage. Remote filenames are not used as local paths.
- Audio, video, original attachments, and executable files are not automatically
  downloaded to disk or launched by the feed.
- API/image byte limits, bounded preview caches, worker budgets, per-host pacing,
  Retry-After, pause/stop behavior, and covered-media rules remain in place.

## Validation

The suite includes real subprocess checks for denied file read/write, sockets,
fork, exec, and limit changes; unchanged synthetic canary files; bounded-output
and timeout tests; safe failure on unsupported platforms; five valid raster
formats; orientation preservation; metadata/trailer removal; image dimension
bombs; invalid worker packets; unsafe URLs; IPv6 translations; reader resource
blocking; JSON limits; and SQL strings treated as data. Existing whole-image,
smooth-scroll, preload, cache, theme, passport, and engine tests also run.

The release smoke test decodes a synthetic PNG through the actual worker entry
point, including in the frozen executable. A successful demo launch by itself
would not establish this image boundary.

## Remaining risks

- The main application is a normal desktop process. Python, TLS, SQLite, Qt text
  rendering, Pillow, bundled codec libraries, and the OS still need updates.
  No claim is made that every parser or dependency is free of vulnerabilities.
- Previews are not virus-scanned, and scrubbing cannot make a vulnerable decoder
  safe before it parses the original input. Restrictions are installed first.
  Pixel patterns can retain hidden information; the app does not extract or
  execute steganographic content.
- An original/media link opens an external browser. Its navigation, redirects,
  downloads, extensions, and login state are outside this worker policy.
- Public servers see the reader's IP, request timing, and User-Agent. Preview
  URLs may be trackers. Public API data and authorship claims are not independently
  authenticated ActivityPub signatures.
- Native artifacts remain unsigned. Checksums identify bytes; they do not by
  themselves authenticate an untrusted download channel or installation.
- Live adversarial-network and independent fuzzing/penetration testing were not
  performed. Synthetic tests are evidence for specific controls, not proof of
  absence of all exploitable paths.

References: [Qt shared security model](https://doc.qt.io/qt-6/shared-security-model.html),
[Linux seccomp filters](https://docs.kernel.org/userspace-api/seccomp_filter.html),
[Pillow security guidance](https://pillow.readthedocs.io/en/stable/handbook/security.html).

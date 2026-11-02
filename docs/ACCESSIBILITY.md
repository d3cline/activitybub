# Accessibility in Activity Bub

Open **⋯ → Accessibility…**, or press **Ctrl+Alt+A**. Settings save locally and
apply to your existing theme and feed. Cancel leaves them unchanged. Reset
accessibility defaults, then Save, restores the original presentation.

| Setting | Effect |
| --- | --- |
| Blue / yellow friendly (tritan) | Neutral surfaces, pink accent, and blue/yellow image remapping |
| Red-sensitive friendly (protan) | Neutral surfaces, blue accent, and red-sensitive image remapping |
| Green-sensitive friendly (deutan) | Neutral surfaces, amber accent, and green-sensitive image remapping |
| Monochrome | Grayscale interface and images |
| Warm colors · less blue | Brown surfaces, cream/amber text, and warmer images with less blue |
| High contrast · dark / light | Strong interface contrast and an image contrast boost without inversion |
| Apply color and contrast settings to images | Enabled by default; turn off for original image colors |
| Text and interface size | 100%, 125%, 150%, 175%, or 200% |
| Reading font / Bold text | System, sans serif, serif, or monospace; optional heavier text |
| Larger buttons and scrollbars | Taller controls, wider scrollbar and slider handles |
| Hide image previews | Remove previews and new preview requests; retain descriptions |
| Reduce motion | Direct wheel scrolling; disable easing and Auto Doom |
| Screen-reader mode | Standard Qt text feed, accessible post summaries, no animated scrolling |

Color and contrast settings affect both the interface and image previews by
default, including profiles saved before 0.5.1. The image-filter checkbox lets
you compare against original colors. Changes apply immediately in the feed and
reader, including cached images, without downloading them again. Source pixels,
aspect ratio, alpha transparency and image descriptions stay intact.

The color remapping is an approximate display aid; results depend on your vision
and the image. It is not a clinical correction or a simulation of impaired
vision. The code estimates lost color information and redistributes it into
other channels. Monochrome converts image pixels to grayscale; warm mode lowers
green and blue. Both high-contrast themes use the same image tone curve without
inverting photos; the image curve follows the selected color remapping.
High contrast takes priority over the interface palette. Text, check marks and
outlines also identify states, so color is not the only signal. At larger sizes,
footer groups stack and the reader scrolls to keep its controls reachable.

## Keyboard and assistive readers

- **F6** focuses the feed. **Up/Down** selects a post; **Enter** opens its reader.
- The reader focuses the native post text, or **Reveal this post** for a covered
  post. **Esc** returns to the feed. Tab and Shift+Tab traverse controls.
- **Ctrl+Plus** (also Ctrl+=), **Ctrl+Minus**, **Ctrl+0** change or reset text size.
- **Ctrl+F** focuses search; **B** saves a selected feed post; **Ctrl+O** opens it.
- Buttons, searches, filters, sliders, the server table, media selection and the
  feed expose names. Feed items expose post text, counts, saved state and provided
  attachment descriptions through Qt's accessibility roles. Missing descriptions
  are reported explicitly. The reader displays descriptions as selectable text.
- Covered CW bodies and descriptions stay hidden from both the visible feed and
  accessibility tree until manually revealed or Auto CW is enabled. Hide CW
  remains a separate filter. Screen-reader mode preserves your CW choice.

Use your operating system's screen reader. Activity Bub provides native Qt
accessibility; it does not install a speech engine. Launch with
`./Activity-Bub --screen-reader` (portable), or
`python -m activity_bub --screen-reader` (source), to enable and save text mode
before opening the window. On Linux this also requests Qt's AT-SPI bridge before
Qt initializes. If enabling the option in a running app does not attach your
reader, restart Activity Bub with your screen reader already running.

The automated tests inspect Qt's actual accessible list items, CW privacy,
keyboard entry/exit, motion changes, saved preferences, 200% layouts and palette
contrast. High-contrast interface text colors reach at least 7:1 against the
configured surfaces. This is not a whole-app accessibility certification. Orca,
NVDA and VoiceOver have not been exercised end to end in this build environment.
OS reader/desktop integration and the usefulness of remote authors' descriptions
still need testing with those tools on real desktops.

## Preview safety

The existing restricted image workers and URL/content checks remain in force.
Image filters only process bounded decoded RGBA pixels with fixed matrix and
lookup-table operations. They do not open image containers, metadata or profiles
in the GUI. Scaled previews share a bounded cache; changing filters invalidates
that cache while retaining the original pixels.
Hide previews cancels queued work; requests already running may finish in their
restricted workers. Screen-reader text mode skips feed preloading; opening a
post can show its preview unless Hide image previews is enabled. Unsupported
platforms still disable remote decoding. See [the security audit](SECURITY-AUDIT.md).

Implementation references: [Qt accessibility roles](https://doc.qt.io/qt-6/qt.html),
[Qt accessibility and platform bridges](https://doc.qt.io/qt-6/qaccessible.html).
Image remapping references: [Machado's simulation matrices, Appendix A](https://www.inf.ufrgs.br/~oliveira/students_dissertations/Masters/Gustavo_Machado_Masters_thesis_UFRGS_2010.pdf),
[error redistribution](https://pmc.ncbi.nlm.nih.gov/articles/PMC5418066/),
and [Pillow raw pixel operations](https://pillow.readthedocs.io/en/stable/reference/Image.html).

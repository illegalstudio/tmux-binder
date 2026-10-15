# tmux-binder logo

The mark combines a binder spine, a sidebar and two terminal panes into an abstract
lowercase b. All three PNG files are 1254 by 1254 pixels and contain the symbol
without a wordmark. The transparent version has a real alpha channel.

| File | Use |
| --- | --- |
| [logo-transparent.png](logo-transparent.png) | Transparent canvas and negative spaces for use on other surfaces |
| [logo-light.png](logo-light.png) | Solid white background with mint and deep teal artwork |
| [logo-dark.png](logo-dark.png) | Solid charcoal background with brighter mint artwork |

The primary brand color is mint **`#34D399`**. The light variant uses deep teal
for contrast; the dark variant uses charcoal **`#0D1117`** and lighter mint accents.
The README selects light and dark variants through a GitHub-compatible
`<picture>` element, with the transparent image as its fallback.

Preserve the square aspect ratio, surrounding space and terminal cutouts. Avoid
stretching the mark or placing it over a visually busy background.

## Generation

Created with the built-in **imagegen** tool. The transparent source was generated
first, then used as the reference for the two opaque background variants.
The selected tool outputs are copied here without raster edits.

<details>
<summary>Prompts for the delivered assets</summary>

### Transparent source

```text
Use case: logo-brand. Create a polished square, mark-only logo for tmux-binder, an open-source tmux sidebar that binds sessions, windows and panes into one navigable tree. A distinctive compact geometric emblem combining a strong vertical binder spine / sidebar on the left with two interconnected terminal panes on the right, subtly forming an abstract lowercase b through negative space. Modern developer-tool identity, disciplined geometry, confident broad strokes, slightly rounded square corners, flat vector-like execution with perfectly clean edges, strong silhouette legible at 32 pixels. Use one restrained mint-green accent (#34D399), with deep teal if a second tone materially improves the form. Center exactly one large emblem with consistent generous square padding. True transparent background, including negative spaces. No wordmark, no text, no lettering, no numbers, no terminal chrome dots, no emoji, no mascot, no gradients, no glow, no shadows, no mockup, no border framing the canvas, no extra symbols, no checkerboard painted into the image. Square 1024 by 1024 artwork suitable for a GitHub project header.
```

### Light background

Reference: `logo-transparent.png`.

```text
Create the LIGHT BACKGROUND version of this exact tmux-binder logo. Preserve the mark's precise silhouette, geometry, scale, location, padding, the mint-green left binder spine, the two deep-teal terminal panes and their terminal prompt cutouts. Place it on a perfectly uniform solid white #FFFFFF square background. The cutouts and gaps must show that same white. Remove any cyan edge fringe or stray pixels. Keep the same recognizable visual identity and clean professional appearance. No text, no new elements, no border, no cast shadow, no backdrop texture. Square image, logo mark only. Keep the emblem centered and the same size as the reference.
```

### Dark background

Reference: `logo-transparent.png`.

```text
Create the DARK BACKGROUND version of this exact tmux-binder logo. Preserve the mark's precise silhouette, geometry, scale, location, padding, tall left binder spine and two stacked terminal panes with terminal prompt cutouts. Place it on a perfectly uniform deep charcoal #0D1117 square background. Adapt only the mark colors for dark-background contrast: use bright mint green #34D399 for the two terminal panes and a slightly lighter mint #6EE7B7 for the left spine, with restrained deep emerald edges if necessary. Every part of the mark must remain clearly visible, not dark teal fading into the dark background. The cutouts and gaps must show the same charcoal background. Remove cyan edge fringe and stray pixels. No text, no new elements, no border, no cast shadow, no texture. Keep the emblem centered and the same size as the reference.
```

</details>

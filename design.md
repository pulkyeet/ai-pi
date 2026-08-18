<!-- fullWidth: false tocVisible: false tableWrap: true -->
\---

version: alpha

name: Firecrawl Clean Data

description: A bright, high-contrast SaaS system with orange energy, crisp neutrals, and understated editorial typography.

colors:

  primary: "#fa5d19"

  secondary: "#262626"

  tertiary: "#e5e7eb"

  neutral: "#f9f9f9"

  surface: "#ffffff"

  on-surface: "#262626"

  muted: "#6b7280"

  border: "#e5e7eb"

  success: "#16a34a"

  error: "#dc2626"

  primary-contrast: "#ffffff"

  primary-soft: "#fff2ec"

typography:

  headline-display:

    fontFamily: "suisse"

    fontSize: "44px"

    fontWeight: 500

    lineHeight: "48px"

    letterSpacing: "-0.22px"

  headline-lg:

    fontFamily: "suisse"

    fontSize: "36px"

    fontWeight: 500

    lineHeight: "40px"

    letterSpacing: "-0.36px"

  headline-md:

    fontFamily: "suisse"

    fontSize: "20px"

    fontWeight: 500

    lineHeight: "24px"

  headline-sm:

    fontFamily: "suisse"

    fontSize: "18px"

    fontWeight: 500

    lineHeight: "22px"

  body-lg:

    fontFamily: "suisse"

    fontSize: "16px"

    fontWeight: 400

    lineHeight: "26px"

    letterSpacing: "-0.09px"

  body-md:

    fontFamily: "suisse"

    fontSize: "16px"

    fontWeight: 400

    lineHeight: "24px"

  body-sm:

    fontFamily: "suisse"

    fontSize: "14px"

    fontWeight: 400

    lineHeight: "20px"

  label-lg:

    fontFamily: "suisse"

    fontSize: "16px"

    fontWeight: 450

    lineHeight: "20px"

  label-md:

    fontFamily: "suisse"

    fontSize: "14px"

    fontWeight: 450

    lineHeight: "18px"

  label-sm:

    fontFamily: "suisse"

    fontSize: "12px"

    fontWeight: 500

    lineHeight: "16px"

    letterSpacing: "0.02em"

  nav-link:

    fontFamily: "suisse"

    fontSize: "16px"

    fontWeight: 450

    lineHeight: "20px"

  caption:

    fontFamily: "suisse"

    fontSize: "12px"

    fontWeight: 400

    lineHeight: "16px"

rounded:

  none: "0px"

  sm: "4px"

  md: "8px"

  lg: "10px"

  xl: "16px"

  full: "9999px"

spacing:

  xs: "6px"

  sm: "16px"

  md: "24px"

  lg: "32px"

  xl: "88px"

  gutter: "24px"

  section: "88px"

components:

  button-primary:

    backgroundColor: "{colors.primary}"

    textColor: "{colors.primary-contrast}"

    typography: "{typography.label-lg}"

    rounded: "{rounded.lg}"

    padding: "8px"

    height: "40px"

  button-primary-hover:

    backgroundColor: "#ff6a2a"

    textColor: "{colors.primary-contrast}"

    rounded: "{rounded.lg}"

  button-secondary:

    backgroundColor: "transparent"

    textColor: "{colors.on-surface}"

    typography: "{typography.label-lg}"

    rounded: "{rounded.lg}"

    padding: "8px"

    height: "40px"

  button-link:

    backgroundColor: "transparent"

    textColor: "{colors.on-surface}"

    typography: "{typography.label-lg}"

    rounded: "{rounded.none}"

    padding: "0px"

  card:

    backgroundColor: "{colors.neutral}"

    textColor: "{colors.on-surface}"

    rounded: "{rounded.sm}"

    padding: "16px"

  input:

    backgroundColor: "{colors.surface}"

    textColor: "{colors.on-surface}"

    typography: "{typography.body-md}"

    rounded: "{rounded.xl}"

    padding: "16px"

    height: "56px"

  chip:

    backgroundColor: "{colors.surface}"

    textColor: "{colors.on-surface}"

    typography: "{typography.label-md}"

    rounded: "{rounded.full}"

    padding: "6px"

  navbar:

    backgroundColor: "{colors.surface}"

    textColor: "{colors.on-surface}"

    height: "64px"

  banner:

    backgroundColor: "{colors.primary}"

    textColor: "{colors.primary-contrast}"

    typography: "{typography.label-sm}"

    rounded: "{rounded.lg}"

    padding: "8px"

  search-panel:

    backgroundColor: "{colors.surface}"

    textColor: "{colors.on-surface}"

    rounded: "{rounded.xl}"

    padding: "16px"

\---



\# Firecrawl Clean Data



\## Overview

Firecrawl presents as a sharp, modern SaaS brand built for technical users, especially developers and teams working with AI and web automation. The tone is confident and efficient rather than playful, with a strong emphasis on clarity, speed, and “clean data” as the core promise. Visual density is moderate: the page uses generous whitespace in the hero while keeping controls compact and highly functional.



\## Colors

\- \*\*Primary (#fa5d19):\*\* A vivid orange-coral used for the announcement bar, main CTA buttons, active accents, and brand energy. It should feel warm, urgent, and highly visible against the otherwise pale interface.

\- \*\*Secondary / On-surface (#262626):\*\* A deep charcoal used for headlines, navigation, body copy, and most interface text. It provides strong contrast without the harshness of pure black.

\- \*\*Neutral (#f9f9f9):\*\* A soft off-white background that keeps the page airy and makes cards and panels feel lightly lifted.

\- \*\*Surface (#ffffff):\*\* Pure white used for interactive surfaces such as inputs, pills, and elevated UI blocks.

\- \*\*Tertiary / Border (#e5e7eb):\*\* A pale gray for borders, dividers, and subtle framing. It is functional and almost invisible, supporting structure without adding visual weight.

\- \*\*Muted (#6b7280):\*\* A secondary text tone for less important labels, helper text, or de-emphasized navigation states.

\- \*\*Primary soft (#fff2ec):\*\* A gentle tint for hover or selected states that need orange affinity without full saturation.

\- \*\*Primary contrast (#ffffff):\*\* White text on orange backgrounds, used for CTA legibility.

\- \*\*Error (#dc2626):\*\* Reserved for destructive states and validation feedback, though the core screen is overwhelmingly neutral and positive.



\## Typography

The system uses Suisse as the primary typeface, with a clean sans-serif fallback stack for broad compatibility. Headings are medium weight and tightly tracked, giving the brand a polished editorial feel without becoming formal or luxurious. Body text is regular weight and slightly looser in line height for readability, while labels and buttons use a subtly heavier 450 weight to feel crisp and usable.



\- \*\*Headline-display:\*\* Large hero messaging, especially the main value proposition.

\- \*\*Headline-lg:\*\* Secondary section headlines and prominent supporting statements.

\- \*\*Headline-md / Headline-sm:\*\* Subheads, card titles, and compact UI headings.

\- \*\*Body-lg:\*\* Primary descriptive copy in the hero and marketing sections.

\- \*\*Body-md / Body-sm:\*\* Supporting text, helper copy, and denser interface prose.

\- \*\*Label-lg / Label-md / Label-sm:\*\* Buttons, pills, nav items, and compact controls.

\- \*\*Nav-link:\*\* Navigation items should stay medium-weight and clean, without excessive letter spacing.

\- \*\*Caption:\*\* Microcopy, annotations, and subdued utility labels.



Uppercase styling is minimal; the system relies more on spacing, weight, and tone than on caps or aggressive tracking.



\## Layout

The page uses a centered, fixed-max-width marketing layout with a very large hero zone and clear vertical progression. Content is stacked in distinct bands: announcement, navigation, hero, interactive demo, then supporting product imagery. Spacing follows a predictable rhythm based on 6px, 16px, 24px, 32px, and a large 88px section gap for major separations.



Cards and embedded widgets favor roomy internal padding, but the overall interface keeps controls compact so the page remains efficient. The design feels grid-aware and modular, with generous side margins and restrained column widths that preserve focus on the hero message.



\## Elevation & Depth

The interface is intentionally shallow and mostly flat. Depth is created through soft borders, slight tonal shifts between white and off-white surfaces, and selective shadowing on buttons and floating panels. The orange primary button carries the most visible depth treatment, while cards rely more on outline clarity than heavy shadow.



This restrained approach keeps the product feeling modern and technical rather than glossy or decorative.



\## Shapes

The shape language is soft and disciplined. Most surfaces use small to medium radii, with cards around 8px, primary buttons around 10px, and inputs/panels closer to 16px or fully pill-like when used as chips. The result is approachable but still engineered, avoiding sharp edges on interactive elements.



Large rounded pills are used for badges, segment controls, and search affordances to make utility elements feel lightweight and touch-friendly.



\## Components

\- \*\*Buttons:\*\* Primary buttons use \`button-primary\` with orange fill, white text, and compact 8px padding for a 40px-tall control. Secondary buttons use \`button-secondary\`, staying transparent or near-transparent with dark text. Link-style actions use \`button-link\` and should remain unboxed. Hover states should brighten the orange slightly, not introduce new colors or heavy shadows.

\- \*\*Cards:\*\* Use \`card\` for light content containers with a 1px border, soft 8px corners, and 16px padding. Cards should feel structural rather than elevated.

\- \*\*Inputs:\*\* Use \`input\` for search fields and form controls. Inputs should be white, softly bordered by surrounding layout rather than visually loud, and large enough to feel comfortable in a marketing/demo context.

\- \*\*Chips / Pills:\*\* Use \`chip\` for category selectors, badges, and small segmented controls. Pills should be compact, rounded fully, and rely on background contrast or subtle borders rather than vivid fills.

\- \*\*Navigation:\*\* Use \`navbar\` for the top shell. Navigation is slim, white, and text-forward, with the sign-up action treated as a subtle button rather than a dominant CTA.

\- \*\*Banner:\*\* Use \`banner\` for announcement strips. These should be highly visible, orange, and short in height with centered copy.

\- \*\*Search panel:\*\* Use \`search-panel\` for prominent embedded search or command surfaces. This block should read as the primary interactive demo area and may include internal chips, helper text, and a right-aligned submit button.



\## Do's and Don'ts

\- Do keep the orange primary color reserved for key actions, highlights, and brand moments.

\- Do preserve the airy off-white background and soft border palette.

\- Do use Suisse consistently for headlines, body, labels, and navigation.

\- Do maintain medium-weight typography with compact but readable line heights.

\- Do prefer subtle depth through borders and tonal separation over strong shadows.

\- Don't introduce dark backgrounds or high-contrast neon accents that break the calm SaaS feel.

\- Don't over-round every element; use pills intentionally and keep most cards/buttons modestly rounded.

\- Don't make primary CTAs overly large or decorative; they should feel efficient and direct.
---
name: Synthetica
colors:
  surface: '#131315'
  surface-dim: '#131315'
  surface-bright: '#39393b'
  surface-container-lowest: '#0e0e10'
  surface-container-low: '#1c1b1d'
  surface-container: '#201f22'
  surface-container-high: '#2a2a2c'
  surface-container-highest: '#353437'
  on-surface: '#e5e1e4'
  on-surface-variant: '#c2c6d6'
  inverse-surface: '#e5e1e4'
  inverse-on-surface: '#313032'
  outline: '#8c909f'
  outline-variant: '#424754'
  surface-tint: '#adc6ff'
  primary: '#adc6ff'
  on-primary: '#002e6a'
  primary-container: '#4d8eff'
  on-primary-container: '#00285d'
  inverse-primary: '#005ac2'
  secondary: '#89ceff'
  on-secondary: '#00344d'
  secondary-container: '#00a2e6'
  on-secondary-container: '#00344e'
  tertiary: '#d0bcff'
  on-tertiary: '#3c0091'
  tertiary-container: '#a078ff'
  on-tertiary-container: '#340080'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#d8e2ff'
  primary-fixed-dim: '#adc6ff'
  on-primary-fixed: '#001a42'
  on-primary-fixed-variant: '#004395'
  secondary-fixed: '#c9e6ff'
  secondary-fixed-dim: '#89ceff'
  on-secondary-fixed: '#001e2f'
  on-secondary-fixed-variant: '#004c6e'
  tertiary-fixed: '#e9ddff'
  tertiary-fixed-dim: '#d0bcff'
  on-tertiary-fixed: '#23005c'
  on-tertiary-fixed-variant: '#5516be'
  background: '#131315'
  on-background: '#e5e1e4'
  surface-variant: '#353437'
typography:
  headline-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-lg-mobile:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  headline-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '500'
    lineHeight: 28px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 26px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 22px
  label-mono:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.05em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  container-max: 1200px
  gutter: 24px
  margin-page: 32px
  stack-sm: 8px
  stack-md: 16px
  stack-lg: 32px
  chat-width: 768px
---

## Brand & Style
The design system is centered on clarity, intelligence, and fluid interaction. It targets a professional audience that requires a distraction-free environment for deep work and AI-assisted creativity. 

The aesthetic blends **Minimalism** with subtle **Glassmorphism** to create a sense of depth and technical sophistication. The interface prioritizes content—specifically text-based chat strings and data visualizations—by using generous whitespace and a restricted color palette. Visual interest is generated through light refraction effects, fine-line borders, and precise typographic scaling rather than heavy decorative elements.

## Colors
The palette is designed for high-endurance usage in both light and dark environments, though the default is a sophisticated "Zinc-Deep" dark mode. 

- **Primary (AI-Blue):** Used for primary actions, active states, and AI identity markers.
- **Surface Tiers:** Uses a Zinc/Slate scale. `Surface-0` (#09090b) for base backgrounds, `Surface-1` (#18181b) for chat bubbles, and `Surface-2` (#27272a) for hover states and elevated cards.
- **Accents:** Secondary Cyan and Tertiary Violet are reserved for code syntax highlighting and multi-modal AI feedback loops.
- **Glass Effects:** Overlays use a 60% opacity version of the surface colors with a 20px backdrop blur.

## Typography
The system utilizes **Inter** for all UI and conversational text to ensure maximum legibility across all display densities. Its high x-height and open counters facilitate long-form reading of AI-generated responses.

**JetBrains Mono** is introduced as a supporting typeface for technical metadata, code blocks, and system status labels, reinforcing the "AI/Machine" narrative through a developer-centric aesthetic. All body text should maintain a generous line height (1.6x) to reduce cognitive load during long sessions.

## Layout & Spacing
The layout follows a **Fixed-Fluid Hybrid** model. The main navigation and sidebar are fixed-width, while the central chat thread is constrained to a `chat-width` of 768px to maintain optimal line lengths for reading.

A standard 8px grid system governs all spatial relationships. 
- **Desktop:** 3-column layout (Navigation / Chat Thread / Context Panel).
- **Mobile:** Single column with 16px horizontal margins.
- **Spacing Philosophy:** Use "Logical Grouping"—related messages have 8px gaps, while speaker changes (User to AI) have 32px gaps to clearly delineate turns in conversation.

## Elevation & Depth
Depth is expressed through **Tonal Layering** and **Glassmorphism** rather than traditional heavy shadows.

- **Level 0 (Base):** Deep Zinc background.
- **Level 1 (Chat Bubbles):** A slightly lighter gray with a 1px "inner-glow" border (White @ 5% opacity).
- **Level 2 (Modals/Popovers):** Semi-transparent background (Zinc-900 @ 80%) with a 24px Backdrop Blur and a subtle 0 4px 20px RGBA(0,0,0,0.4) shadow.
- **Dividers:** Use 1px solid lines in Zinc-800 for structural separation, avoiding shadows where possible to maintain a "flat-modern" feel.

## Shapes
The design system employs a **Rounded** corner language (8px to 24px) to soften the technical nature of AI.

- **Standard Elements (Buttons, Inputs):** 8px (`rounded-md`).
- **Chat Bubbles:** 16px (`rounded-lg`) on three corners, with the fourth corner (the "tail" side) reduced to 4px to indicate directionality.
- **Large Containers/Cards:** 24px (`rounded-xl`).
- **Pill Badges:** Fully rounded (9999px) for status indicators and chips.

## Components

- **Chat Input:** A floating, glass-morphic text area with an 8px border-radius. It should expand vertically but maintain a maximum height before scrolling. Primary "Send" button is a 32px square/circle within the input field.
- **AI Response Bubbles:** Minimalist styling with a distinct border or a very subtle gradient (Zinc-900 to Zinc-800).
- **Buttons:** 
  - *Primary:* Solid AI-Blue with white text.
  - *Ghost:* 1px Zinc-700 border, no background, primary blue text on hover.
- **Action Chips:** Used for "Suggested Prompts" below the input. These should be 1px bordered Zinc-800 with a subtle hover lift.
- **Code Blocks:** Darker background (Black) with JetBrains Mono, syntax highlighting using the Secondary and Tertiary colors, and a "Copy" button appearing on hover in the top-right corner.
- **Feedback Icons:** Thumb up/down and "Regenerate" actions should remain low-contrast (Zinc-500) until hovered, reducing visual noise in the thread.
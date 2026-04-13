# AutoWiki Frontend Redesign — Spec

**Status:** COMPLETE (2026-04-12)
**Project:** AutoWiki

---

## 1. Goal

Replicate the clean, modern, light-mode-first visual style of `deepwiki.com` across the AutoWiki Home Page and Wiki Page, integrating search, repository metadata, and a three-column navigation layout.

---

## 2. Global Theme (Tailwind v4)

- **Mode:** Light-mode-first (default).
- **Primary Color:** Indigo (`oklch(0.55 0.20 260)`).
- **Background:** White (`oklch(1 0 0)`).
- **Text:** Deep slate (`oklch(0.15 0.02 260)`).
- **Radii:** `0.75rem` (rounded-xl).

---

## 3. Page Layouts

### 3.1 Home Page
- **Hero:** Centered search bar (`IndexForm`) with large typography.
- **Language Switcher:** Top-right toggle for EN/中文.
- **Repo Grid:** Responsive grid of `RepoCard` components showing rich metadata (stars, language, updated time).

### 3.2 Wiki Page (Three-Column)
- **Left Column (Sidebar):** Sticky navigation tree with hierarchical pages.
- **Center Column (Content):** Constrained-width Markdown content (`max-w-4xl`).
- **Right Column (TOC):** Sticky Table of Contents generated from headers.

---

## 4. Interactive Components

- **Chat Drawer:** Slide-out right panel for Q&A.
- **Dependency Graph:** Full-screen interactive ReactFlow graph.
- **Mermaid Diagrams:** Client-side rendered diagrams embedded in wiki pages.
- **Progress Bar:** Real-time job status with detailed step descriptions.

---

## Status: Implemented (Wiki Optimization Phase)

This design was fully implemented in the Wiki Optimization Phase.

# AutoWiki Frontend Design Specification

## Overview
This document outlines the visual design and layout structure for the AutoWiki frontend, heavily inspired by the clean, modern aesthetics of platforms like `deepwiki.com` and `zread.ai`. The goal is to provide a highly readable, intuitive interface for browsing AI-generated repository documentation.

## Global Styles (Light Mode Preferred)
*   **Theme:** Primarily Light Mode. Dark mode will be supported but light mode is the default and optimized target.
*   **Color Palette:**
    *   Background: Clean white (`bg-white` or `bg-slate-50`).
    *   Text: High contrast for readability (`text-slate-900` for primary, `text-slate-500` for secondary).
    *   Borders: Subtle and unobtrusive (`border-slate-200`).
    *   Accents: A primary color (e.g., a subdued blue or indigo) for active states and primary buttons.
*   **Typography:** Sans-serif, utilizing system fonts or Inter/Geist. Focus on hierarchy with clear distinctions between headers (bold, larger) and body text (regular, highly legible line height).
*   **Spacing:** Generous padding and margins (`p-4`, `p-6`, `gap-4`) to create a breathable, uncluttered interface.

## 1. Home Page Layout

The Home Page serves as the entry point, focusing on search and discovery.

### Structure
1.  **Hero Section:**
    *   Centered alignment.
    *   Clear, concise headline (e.g., "Explore Open Source Knowledge").
    *   **Search Bar:** Prominent and large. Supports fuzzy search for indexed repositories and accepts direct GitHub URLs for immediate indexing/navigation.
2.  **Recent/Featured Repositories (The Grid):**
    *   Displays the 20 most recently indexed projects.
    *   Layout: A responsive grid (e.g., 2 columns on medium screens, 3-4 columns on large screens).
    *   **Repository Cards:**
        *   Clean card design with subtle borders and hover effects (e.g., slight elevation or border color change).
        *   **Content:**
            *   Repository Name (Owner/Repo format).
            *   Brief description (truncated to 2 lines).
            *   **Metadata Footer:** Language icon/color, Star count (e.g., ⭐ 12.5k), and Last Updated time (e.g., "Indexed 2 hrs ago").

## 2. Wiki Page Layout (Three-Column)

The Wiki Page prioritizes deep reading and structural navigation.

### Structure
The layout is divided into three distinct columns:

1.  **Left Column: Global Sidebar (Navigation)**
    *   Fixed position or sticky.
    *   Contains the hierarchical "Wiki Plan" (the directory/page structure of the documentation).
    *   Clear visual indication of the currently active page.
    *   Collapsible sections for deeply nested content.
    *   Width: ~250px - 300px.

2.  **Center Column: Main Content Area**
    *   Scrollable.
    *   Max-width constrained for optimal reading (e.g., `max-w-3xl` or `max-w-4xl`).
    *   Rich Markdown rendering (headers, code blocks with syntax highlighting, tables, lists).
    *   Generous line height and paragraph spacing.

3.  **Right Column: "On This Page" (Table of Contents)**
    *   Sticky position.
    *   Dynamically generated from the Markdown headers (`<h2>`, `<h3>`) of the *current* page.
    *   Highlights the active section as the user scrolls through the main content.
    *   Provides quick intra-page navigation.
    *   Width: ~200px - 250px. Hidden on smaller screens.

## Interactive Elements
*   **Search:** Quick navigation modal (Cmd+K/Ctrl+K) accessible from anywhere, matching the Home Page search capabilities.
*   **Transitions:** Fast, near-instantaneous page loads (leveraging Next.js routing) with subtle state changes on interactive elements.

## Implementation Notes (Tailwind CSS)
The design will be implemented strictly using Tailwind CSS utility classes, adhering to the project's existing v4 configuration (CSS-only approach).

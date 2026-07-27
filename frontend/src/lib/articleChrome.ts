/** Shared reading-mode flag so AppShell can collapse chrome on articles. */
import { writable } from 'svelte/store'

export const articleChromeCollapsed = writable(false)

import { createContext } from 'react';

/** DOM node MainLayout renders below the global top bar, for PageHeaderPortal to target. */
export const HeaderPortalContext = createContext<HTMLDivElement | null>(null);

import { createContext } from 'react';
import type { ReactNode } from 'react';

export interface PageHeaderData {
  title?: string;
  description?: string;
  backUrl?: string;
  icon?: ReactNode;
  /** Extra className applied to MainLayout's <main> for this route. */
  bodyClassName?: string;
}

export interface PageHeaderContextValue {
  header: PageHeaderData;
  setHeader: (data: PageHeaderData) => void;
}

export const PageHeaderContext = createContext<PageHeaderContextValue>({
  header: {},
  setHeader: () => {},
});


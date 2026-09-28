import { useState } from 'react';
import type { ReactNode } from 'react';
import { PageHeaderContext } from './PageHeaderContext';
import type { PageHeaderData } from './PageHeaderContext';

export default function PageHeaderProvider({ children }: { children: ReactNode }) {
  const [header, setHeader] = useState<PageHeaderData>({});

  return (
    <PageHeaderContext.Provider value={{ header, setHeader }}>
      {children}
    </PageHeaderContext.Provider>
  );
}

import { useContext } from 'react';
import { PageHeaderContext } from './PageHeaderContext';

export function usePageHeader() {
  return useContext(PageHeaderContext);
}

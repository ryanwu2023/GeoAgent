import type {Metric} from './types';

export const metricCategory = (m: Metric) => {
  if (m.bucket === 'polling' ||
      ['RCPPTAPP_approve', 'taco_trump_approval'].includes(m.series || '') ||
      /特朗普.*支持率|总统支持率/.test(m.name)) return '民调';
  if (m.bucket === 'shipping') return '动态';
  if (m.shared_market || ['market', 'equity', 'rates', 'credit', 'commodity', 'fx'].includes(m.bucket || '')) return '经济';
  return '军事';
};

import { endpoint } from 'src/app/api';
import { Model } from '@alephdata/vis2';
import asyncActionCreator from './asyncActionCreator';

export const fetchMetadata = asyncActionCreator(
  () => async () => {
    const response = await endpoint.get('metadata');
    const { schemata, ...metadata } = response.data;
    const model = new Model(schemata);
    return {
      metadata: {
        ...metadata,
        schemata: model,
      },
    };
  },
  { name: 'FETCH_METADATA' },
);

export const fetchStatistics = asyncActionCreator(() => async () => {
  const response = await endpoint.get('statistics');
  return { statistics: response.data };
}, { name: 'FETCH_STATISTICS' });

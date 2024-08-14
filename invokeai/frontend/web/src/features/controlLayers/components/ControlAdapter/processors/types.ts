import type { FilterConfig } from 'features/controlLayers/store/types';

export type ProcessorComponentProps<T extends FilterConfig> = {
  onChange: (config: T) => void;
  config: T;
};

import { MenuItem } from '@invoke-ai/ui-library';
import { createMemoizedSelector } from 'app/store/createMemoizedSelector';
import { useAppDispatch, useAppSelector } from 'app/store/storeHooks';
import { useEntityIdentifierContext } from 'features/controlLayers/contexts/EntityIdentifierContext';
import { useCanvasIsBusy } from 'features/controlLayers/hooks/useCanvasIsBusy';
import {
  rgIPAdapterAdded,
  rgNegativePromptChanged,
  rgPositivePromptChanged,
} from 'features/controlLayers/store/canvasSlice';
import { selectCanvasSlice, selectEntity } from 'features/controlLayers/store/selectors';
import { memo, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';

export const RegionalGuidanceMenuItemsAddPromptsAndIPAdapter = memo(() => {
  const entityIdentifier = useEntityIdentifierContext('regional_guidance');
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const isBusy = useCanvasIsBusy();
  const selectValidActions = useMemo(
    () =>
      createMemoizedSelector(selectCanvasSlice, (canvas) => {
        const entity = selectEntity(canvas, entityIdentifier);
        return {
          canAddPositivePrompt: entity?.positivePrompt === null,
          canAddNegativePrompt: entity?.negativePrompt === null,
        };
      }),
    [entityIdentifier]
  );
  const validActions = useAppSelector(selectValidActions);
  const addPositivePrompt = useCallback(() => {
    dispatch(rgPositivePromptChanged({ entityIdentifier, prompt: '' }));
  }, [dispatch, entityIdentifier]);
  const addNegativePrompt = useCallback(() => {
    dispatch(rgNegativePromptChanged({ entityIdentifier, prompt: '' }));
  }, [dispatch, entityIdentifier]);
  const addIPAdapter = useCallback(() => {
    dispatch(rgIPAdapterAdded({ entityIdentifier }));
  }, [dispatch, entityIdentifier]);

  return (
    <>
      <MenuItem onClick={addPositivePrompt} isDisabled={!validActions.canAddPositivePrompt || isBusy}>
        {t('controlLayers.addPositivePrompt')}
      </MenuItem>
      <MenuItem onClick={addNegativePrompt} isDisabled={!validActions.canAddNegativePrompt || isBusy}>
        {t('controlLayers.addNegativePrompt')}
      </MenuItem>
      <MenuItem onClick={addIPAdapter} isDisabled={isBusy}>
        {t('controlLayers.addIPAdapter')}
      </MenuItem>
    </>
  );
});

RegionalGuidanceMenuItemsAddPromptsAndIPAdapter.displayName = 'RegionalGuidanceMenuItemsExtra';

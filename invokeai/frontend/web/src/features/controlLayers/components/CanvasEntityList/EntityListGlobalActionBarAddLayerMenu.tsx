import { IconButton, Menu, MenuButton, MenuItem, MenuList } from '@invoke-ai/ui-library';
import { useAppDispatch } from 'app/store/storeHooks';
import { useDefaultIPAdapter } from 'features/controlLayers/hooks/useLayerControlAdapter';
import {
  controlLayerAdded,
  inpaintMaskAdded,
  ipaAdded,
  rasterLayerAdded,
  rgAdded,
} from 'features/controlLayers/store/canvasSlice';
import { memo, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { PiPlusBold } from 'react-icons/pi';

export const EntityListGlobalActionBarAddLayerMenu = memo(() => {
  const { t } = useTranslation();
  const dispatch = useAppDispatch();
  const defaultIPAdapter = useDefaultIPAdapter();
  const addInpaintMask = useCallback(() => {
    dispatch(inpaintMaskAdded({ isSelected: true }));
  }, [dispatch]);
  const addRegionalGuidance = useCallback(() => {
    dispatch(rgAdded({ isSelected: true }));
  }, [dispatch]);
  const addRasterLayer = useCallback(() => {
    dispatch(rasterLayerAdded({ isSelected: true }));
  }, [dispatch]);
  const addControlLayer = useCallback(() => {
    dispatch(controlLayerAdded({ isSelected: true }));
  }, [dispatch]);
  const addIPAdapter = useCallback(() => {
    const overrides = { ipAdapter: defaultIPAdapter };
    dispatch(ipaAdded({ isSelected: true, overrides }));
  }, [defaultIPAdapter, dispatch]);

  return (
    <Menu>
      <MenuButton
        as={IconButton}
        size="sm"
        variant="link"
        alignSelf="stretch"
        tooltip={t('controlLayers.addLayer')}
        aria-label={t('controlLayers.addLayer')}
        icon={<PiPlusBold />}
        data-testid="control-layers-add-layer-menu-button"
      />
      <MenuList>
        <MenuItem icon={<PiPlusBold />} onClick={addInpaintMask}>
          {t('controlLayers.inpaintMask')}
        </MenuItem>
        <MenuItem icon={<PiPlusBold />} onClick={addRegionalGuidance}>
          {t('controlLayers.regionalGuidance')}
        </MenuItem>
        <MenuItem icon={<PiPlusBold />} onClick={addRasterLayer}>
          {t('controlLayers.rasterLayer')}
        </MenuItem>
        <MenuItem icon={<PiPlusBold />} onClick={addControlLayer}>
          {t('controlLayers.controlLayer')}
        </MenuItem>
        <MenuItem icon={<PiPlusBold />} onClick={addIPAdapter}>
          {t('controlLayers.globalIPAdapter')}
        </MenuItem>
      </MenuList>
    </Menu>
  );
});

EntityListGlobalActionBarAddLayerMenu.displayName = 'EntityListGlobalActionBarAddLayerMenu';

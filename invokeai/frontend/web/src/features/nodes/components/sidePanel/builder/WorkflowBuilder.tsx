import { Button, Flex } from '@invoke-ai/ui-library';
import { useAppDispatch, useAppSelector } from 'app/store/storeHooks';
import ScrollableContent from 'common/components/OverlayScrollbars/ScrollableContent';
import { FormElementComponent } from 'features/nodes/components/sidePanel/builder/ContainerElementComponent';
import { useMonitorForFormElementDnd } from 'features/nodes/components/sidePanel/builder/use-builder-dnd';
import { formLoaded, formModeToggled, selectWorkflowFormMode } from 'features/nodes/store/workflowSlice';
import { elements, rootElementId } from 'features/nodes/types/workflow';
import { memo, useCallback, useEffect } from 'react';

export const WorkflowBuilder = memo(() => {
  const dispatch = useAppDispatch();
  const mode = useAppSelector(selectWorkflowFormMode);
  useMonitorForFormElementDnd();

  useEffect(() => {
    // dispatch(formReset());
    dispatch(formLoaded({ elements, rootElementId }));
  }, [dispatch]);
  return (
    <ScrollableContent>
      <Flex w="full" justifyContent="center">
        <Flex flexDir="column" w={mode === 'view' ? '512px' : 'min-content'} minW="512px">
          <ToggleModeButton />
          {rootElementId && <FormElementComponent id={rootElementId} />}
        </Flex>
      </Flex>
    </ScrollableContent>
  );
});

WorkflowBuilder.displayName = 'WorkflowBuilder';

const ToggleModeButton = memo(() => {
  const dispatch = useAppDispatch();
  const mode = useAppSelector(selectWorkflowFormMode);

  const onClick = useCallback(() => {
    dispatch(formModeToggled());
  }, [dispatch]);

  return <Button onClick={onClick}>{mode === 'view' ? 'Edit' : 'View'}</Button>;
});
ToggleModeButton.displayName = 'ToggleModeButton';

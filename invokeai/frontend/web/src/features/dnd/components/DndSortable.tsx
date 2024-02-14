import type { DragEndEvent } from '@dnd-kit/core';
import { MouseSensor, TouchSensor, useSensor, useSensors } from '@dnd-kit/core';
import { SortableContext } from '@dnd-kit/sortable';
import type { PropsWithChildren } from 'react';
import { memo } from 'react';

import { DndContextTypesafe } from './DndContextTypesafe';

type Props = PropsWithChildren & {
  items: string[];
  onDragEnd(event: DragEndEvent): void;
};

const DndSortable = (props: Props) => {
  const mouseSensor = useSensor(MouseSensor, {
    activationConstraint: { distance: 10 },
  });

  const touchSensor = useSensor(TouchSensor, {
    activationConstraint: { distance: 10 },
  });

  const sensors = useSensors(mouseSensor, touchSensor);

  return (
    <DndContextTypesafe sensors={sensors}>
      <SortableContext items={props.items}>{props.children}</SortableContext>
    </DndContextTypesafe>
  );
};

export default memo(DndSortable);

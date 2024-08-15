import type { CanvasManager } from 'features/controlLayers/konva/CanvasManager';
import {
  alignCoordForTool,
  getObjectId,
  getScaledCursorPosition,
  offsetCoord,
} from 'features/controlLayers/konva/util';
import type {
  CanvasControlLayerState,
  CanvasInpaintMaskState,
  CanvasRasterLayerState,
  CanvasRegionalGuidanceState,
  CanvasV2State,
  Coordinate,
  RgbaColor,
  Tool,
} from 'features/controlLayers/store/types';
import { isDrawableEntity, isDrawableEntityAdapter } from 'features/controlLayers/store/types';
import type Konva from 'konva';
import type { KonvaEventObject } from 'konva/lib/Node';
import { clamp } from 'lodash-es';

import { BRUSH_SPACING_TARGET_SCALE, CANVAS_SCALE_BY } from './constants';

/**
 * Updates the last cursor position atom with the current cursor position, returning the new position or `null` if the
 * cursor is not over the stage.
 * @param stage The konva stage
 * @param setLastCursorPos The callback to store the cursor pos
 */
const updateLastCursorPos = (
  stage: Konva.Stage,
  setLastCursorPos: CanvasManager['stateApi']['$lastCursorPos']['set']
) => {
  const pos = getScaledCursorPosition(stage);
  if (!pos) {
    return null;
  }
  setLastCursorPos(pos);
  return pos;
};

const calculateNewBrushSize = (brushSize: number, delta: number) => {
  // This equation was derived by fitting a curve to the desired brush sizes and deltas
  // see https://github.com/invoke-ai/InvokeAI/pull/5542#issuecomment-1915847565
  const targetDelta = Math.sign(delta) * 0.7363 * Math.pow(1.0394, brushSize);
  // This needs to be clamped to prevent the delta from getting too large
  const finalDelta = clamp(targetDelta, -20, 20);
  // The new brush size is also clamped to prevent it from getting too large or small
  const newBrushSize = clamp(brushSize + finalDelta, 1, 500);

  return newBrushSize;
};

const getNextPoint = (
  currentPos: Coordinate,
  toolState: CanvasV2State['tool'],
  lastAddedPoint: Coordinate | null
): Coordinate | null => {
  // Continue the last line
  const minSpacingPx =
    toolState.selected === 'brush'
      ? toolState.brush.width * BRUSH_SPACING_TARGET_SCALE
      : toolState.eraser.width * BRUSH_SPACING_TARGET_SCALE;

  if (lastAddedPoint) {
    // Dispatching redux events impacts perf substantially - using brush spacing keeps dispatches to a reasonable number
    if (Math.hypot(lastAddedPoint.x - currentPos.x, lastAddedPoint.y - currentPos.y) < minSpacingPx) {
      return null;
    }
  }

  return currentPos;
};

const getLastPointOfLine = (points: number[]): Coordinate | null => {
  if (points.length < 2) {
    return null;
  }
  const x = points[points.length - 2];
  const y = points[points.length - 1];
  if (x === undefined || y === undefined) {
    return null;
  }
  return { x, y };
};

const getLastPointOfLastLineOfEntity = (
  entity: CanvasRasterLayerState | CanvasControlLayerState | CanvasRegionalGuidanceState | CanvasInpaintMaskState,
  tool: Tool
): Coordinate | null => {
  const lastObject = entity.objects[entity.objects.length - 1];

  if (!lastObject) {
    return null;
  }

  if (
    !(
      (lastObject.type === 'brush_line' && tool === 'brush') ||
      (lastObject.type === 'eraser_line' && tool === 'eraser')
    )
  ) {
    // If the last object type and current tool do not match, we cannot continue the line
    return null;
  }

  if (lastObject.points.length < 2) {
    return null;
  }
  const x = lastObject.points[lastObject.points.length - 2];
  const y = lastObject.points[lastObject.points.length - 1];
  if (x === undefined || y === undefined) {
    return null;
  }
  return { x, y };
};

const getColorUnderCursor = (stage: Konva.Stage): RgbaColor | null => {
  const pos = stage.getPointerPosition();
  if (!pos) {
    return null;
  }
  const ctx = stage
    .toCanvas({ x: pos.x, y: pos.y, width: 1, height: 1, imageSmoothingEnabled: false })
    .getContext('2d');
  if (!ctx) {
    return null;
  }
  const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
  if (r === undefined || g === undefined || b === undefined || a === undefined) {
    return null;
  }

  return { r, g, b, a };
};

export const setStageEventHandlers = (manager: CanvasManager): (() => void) => {
  const { stage, stateApi } = manager;
  const {
    getToolState,
    setTool,
    setToolBuffer,
    $isMouseDown,
    $lastMouseDownPos,
    $lastCursorPos,
    $lastAddedPoint,
    $stageAttrs,
    $spaceKey,
    getBbox,
    getSettings,
    setBrushWidth,
    setEraserWidth,
    getCurrentFill,
    getSelectedEntity,
  } = stateApi;

  function getIsPrimaryMouseDown(e: KonvaEventObject<MouseEvent>) {
    return e.evt.buttons === 1;
  }

  function getClip(
    entity: CanvasRegionalGuidanceState | CanvasControlLayerState | CanvasRasterLayerState | CanvasInpaintMaskState
  ) {
    const settings = getSettings();
    const bboxRect = getBbox().rect;

    if (settings.clipToBbox) {
      return {
        x: bboxRect.x - entity.position.x,
        y: bboxRect.y - entity.position.y,
        width: bboxRect.width,
        height: bboxRect.height,
      };
    } else {
      return {
        x: -stage.x() / stage.scaleX() - entity.position.x,
        y: -stage.y() / stage.scaleY() - entity.position.y,
        width: stage.width() / stage.scaleX(),
        height: stage.height() / stage.scaleY(),
      };
    }
  }

  //#region mouseenter
  stage.on('mouseenter', () => {
    manager.preview.tool.render();
  });

  //#region mousedown
  stage.on('mousedown', async (e) => {
    $isMouseDown.set(true);
    const toolState = getToolState();
    const pos = updateLastCursorPos(stage, $lastCursorPos.set);
    const selectedEntity = getSelectedEntity();

    if (toolState.selected === 'eyeDropper') {
      const color = getColorUnderCursor(stage);
      manager.stateApi.$colorUnderCursor.set(color);
      if (color) {
        manager.stateApi.setFill(color);
      }
    }

    if (
      pos &&
      selectedEntity &&
      isDrawableEntity(selectedEntity.state) &&
      !$spaceKey.get() &&
      getIsPrimaryMouseDown(e)
    ) {
      $lastMouseDownPos.set(pos);
      const normalizedPoint = offsetCoord(pos, selectedEntity.state.position);

      if (toolState.selected === 'brush') {
        const lastLinePoint = getLastPointOfLastLineOfEntity(selectedEntity.state, toolState.selected);
        const alignedPoint = alignCoordForTool(normalizedPoint, toolState.brush.width);
        if (e.evt.shiftKey && lastLinePoint) {
          // Create a straight line from the last line point
          if (selectedEntity.adapter.renderer.buffer) {
            selectedEntity.adapter.renderer.commitBuffer();
          }

          await selectedEntity.adapter.renderer.setBuffer({
            id: getObjectId('brush_line', true),
            type: 'brush_line',
            points: [
              // The last point of the last line is already normalized to the entity's coordinates
              lastLinePoint.x,
              lastLinePoint.y,
              alignedPoint.x,
              alignedPoint.y,
            ],
            strokeWidth: toolState.brush.width,
            color: getCurrentFill(),
            clip: getClip(selectedEntity.state),
          });
        } else {
          if (selectedEntity.adapter.renderer.buffer) {
            selectedEntity.adapter.renderer.commitBuffer();
          }
          await selectedEntity.adapter.renderer.setBuffer({
            id: getObjectId('brush_line', true),
            type: 'brush_line',
            points: [alignedPoint.x, alignedPoint.y],
            strokeWidth: toolState.brush.width,
            color: getCurrentFill(),
            clip: getClip(selectedEntity.state),
          });
        }
        $lastAddedPoint.set(alignedPoint);
      }

      if (toolState.selected === 'eraser') {
        const lastLinePoint = getLastPointOfLastLineOfEntity(selectedEntity.state, toolState.selected);
        const alignedPoint = alignCoordForTool(normalizedPoint, toolState.eraser.width);
        if (e.evt.shiftKey && lastLinePoint) {
          // Create a straight line from the last line point
          if (selectedEntity.adapter.renderer.buffer) {
            selectedEntity.adapter.renderer.commitBuffer();
          }
          await selectedEntity.adapter.renderer.setBuffer({
            id: getObjectId('eraser_line', true),
            type: 'eraser_line',
            points: [
              // The last point of the last line is already normalized to the entity's coordinates
              lastLinePoint.x,
              lastLinePoint.y,
              alignedPoint.x,
              alignedPoint.y,
            ],
            strokeWidth: toolState.eraser.width,
            clip: getClip(selectedEntity.state),
          });
        } else {
          if (selectedEntity.adapter.renderer.buffer) {
            selectedEntity.adapter.renderer.commitBuffer();
          }
          await selectedEntity.adapter.renderer.setBuffer({
            id: getObjectId('eraser_line', true),
            type: 'eraser_line',
            points: [alignedPoint.x, alignedPoint.y],
            strokeWidth: toolState.eraser.width,
            clip: getClip(selectedEntity.state),
          });
        }
        $lastAddedPoint.set(alignedPoint);
      }

      if (toolState.selected === 'rect') {
        if (selectedEntity.adapter.renderer.buffer) {
          selectedEntity.adapter.renderer.commitBuffer();
        }
        await selectedEntity.adapter.renderer.setBuffer({
          id: getObjectId('rect', true),
          type: 'rect',
          rect: { x: Math.round(normalizedPoint.x), y: Math.round(normalizedPoint.y), width: 0, height: 0 },
          color: getCurrentFill(),
        });
      }
    }
    manager.preview.tool.render();
  });

  //#region mouseup
  stage.on('mouseup', () => {
    $isMouseDown.set(false);
    const pos = $lastCursorPos.get();
    const selectedEntity = getSelectedEntity();

    if (pos && selectedEntity && isDrawableEntity(selectedEntity.state) && !$spaceKey.get()) {
      const toolState = getToolState();

      if (toolState.selected === 'brush') {
        const drawingBuffer = selectedEntity.adapter.renderer.buffer;
        if (drawingBuffer?.type === 'brush_line') {
          selectedEntity.adapter.renderer.commitBuffer();
        } else {
          selectedEntity.adapter.renderer.clearBuffer();
        }
      }

      if (toolState.selected === 'eraser') {
        const drawingBuffer = selectedEntity.adapter.renderer.buffer;
        if (drawingBuffer?.type === 'eraser_line') {
          selectedEntity.adapter.renderer.commitBuffer();
        } else {
          selectedEntity.adapter.renderer.clearBuffer();
        }
      }

      if (toolState.selected === 'rect') {
        const drawingBuffer = selectedEntity.adapter.renderer.buffer;
        if (drawingBuffer?.type === 'rect') {
          selectedEntity.adapter.renderer.commitBuffer();
        } else {
          selectedEntity.adapter.renderer.clearBuffer();
        }
      }

      $lastMouseDownPos.set(null);
    }

    manager.preview.tool.render();
  });

  //#region mousemove
  stage.on('mousemove', async (e) => {
    const toolState = getToolState();
    const pos = updateLastCursorPos(stage, $lastCursorPos.set);
    const selectedEntity = getSelectedEntity();

    if (toolState.selected === 'eyeDropper') {
      const color = getColorUnderCursor(stage);
      manager.stateApi.$colorUnderCursor.set(color);
    }

    if (
      pos &&
      selectedEntity &&
      isDrawableEntity(selectedEntity.state) &&
      selectedEntity.adapter &&
      isDrawableEntityAdapter(selectedEntity.adapter) &&
      !$spaceKey.get() &&
      getIsPrimaryMouseDown(e)
    ) {
      if (toolState.selected === 'brush') {
        const drawingBuffer = selectedEntity.adapter.renderer.buffer;
        if (drawingBuffer) {
          if (drawingBuffer.type === 'brush_line') {
            const lastPoint = getLastPointOfLine(drawingBuffer.points);
            const nextPoint = getNextPoint(pos, toolState, lastPoint);
            if (lastPoint && nextPoint) {
              const normalizedPoint = offsetCoord(nextPoint, selectedEntity.state.position);
              const alignedPoint = alignCoordForTool(normalizedPoint, toolState.brush.width);
              // Do not add duplicate points
              if (lastPoint.x !== alignedPoint.x || lastPoint.y !== alignedPoint.y) {
                drawingBuffer.points.push(alignedPoint.x, alignedPoint.y);
                await selectedEntity.adapter.renderer.setBuffer(drawingBuffer);
                $lastAddedPoint.set(alignedPoint);
              }
            }
          } else {
            selectedEntity.adapter.renderer.clearBuffer();
          }
        } else {
          if (selectedEntity.adapter.renderer.buffer) {
            selectedEntity.adapter.renderer.commitBuffer();
          }
          const normalizedPoint = offsetCoord(pos, selectedEntity.state.position);
          const alignedPoint = alignCoordForTool(normalizedPoint, toolState.brush.width);
          await selectedEntity.adapter.renderer.setBuffer({
            id: getObjectId('brush_line', true),
            type: 'brush_line',
            points: [alignedPoint.x, alignedPoint.y],
            strokeWidth: toolState.brush.width,
            color: getCurrentFill(),
            clip: getClip(selectedEntity.state),
          });
          $lastAddedPoint.set(alignedPoint);
        }
      }

      if (toolState.selected === 'eraser') {
        const drawingBuffer = selectedEntity.adapter.renderer.buffer;
        if (drawingBuffer) {
          if (drawingBuffer.type === 'eraser_line') {
            const lastPoint = getLastPointOfLine(drawingBuffer.points);
            const nextPoint = getNextPoint(pos, toolState, lastPoint);
            if (lastPoint && nextPoint) {
              const normalizedPoint = offsetCoord(nextPoint, selectedEntity.state.position);
              const alignedPoint = alignCoordForTool(normalizedPoint, toolState.eraser.width);
              // Do not add duplicate points
              if (lastPoint.x !== alignedPoint.x || lastPoint.y !== alignedPoint.y) {
                drawingBuffer.points.push(alignedPoint.x, alignedPoint.y);
                await selectedEntity.adapter.renderer.setBuffer(drawingBuffer);
                $lastAddedPoint.set(alignedPoint);
              }
            }
          } else {
            selectedEntity.adapter.renderer.clearBuffer();
          }
        } else {
          if (selectedEntity.adapter.renderer.buffer) {
            selectedEntity.adapter.renderer.commitBuffer();
          }
          const normalizedPoint = offsetCoord(pos, selectedEntity.state.position);
          const alignedPoint = alignCoordForTool(normalizedPoint, toolState.eraser.width);
          await selectedEntity.adapter.renderer.setBuffer({
            id: getObjectId('eraser_line', true),
            type: 'eraser_line',
            points: [alignedPoint.x, alignedPoint.y],
            strokeWidth: toolState.eraser.width,
            clip: getClip(selectedEntity.state),
          });
          $lastAddedPoint.set(alignedPoint);
        }
      }

      if (toolState.selected === 'rect') {
        const drawingBuffer = selectedEntity.adapter.renderer.buffer;
        if (drawingBuffer) {
          if (drawingBuffer.type === 'rect') {
            const normalizedPoint = offsetCoord(pos, selectedEntity.state.position);
            drawingBuffer.rect.width = Math.round(normalizedPoint.x - drawingBuffer.rect.x);
            drawingBuffer.rect.height = Math.round(normalizedPoint.y - drawingBuffer.rect.y);
            await selectedEntity.adapter.renderer.setBuffer(drawingBuffer);
          } else {
            selectedEntity.adapter.renderer.clearBuffer();
          }
        }
      }
    }
    manager.preview.tool.render();
  });

  //#region mouseleave
  stage.on('mouseleave', async (e) => {
    const pos = updateLastCursorPos(stage, $lastCursorPos.set);
    $lastCursorPos.set(null);
    $lastMouseDownPos.set(null);
    const selectedEntity = getSelectedEntity();
    const toolState = getToolState();

    if (
      pos &&
      selectedEntity &&
      isDrawableEntity(selectedEntity.state) &&
      !$spaceKey.get() &&
      getIsPrimaryMouseDown(e)
    ) {
      const drawingBuffer = selectedEntity.adapter.renderer.buffer;
      const normalizedPoint = offsetCoord(pos, selectedEntity.state.position);
      if (toolState.selected === 'brush' && drawingBuffer?.type === 'brush_line') {
        const alignedPoint = alignCoordForTool(normalizedPoint, toolState.brush.width);
        drawingBuffer.points.push(alignedPoint.x, alignedPoint.y);
        await selectedEntity.adapter.renderer.setBuffer(drawingBuffer);
        selectedEntity.adapter.renderer.commitBuffer();
      } else if (toolState.selected === 'eraser' && drawingBuffer?.type === 'eraser_line') {
        const alignedPoint = alignCoordForTool(normalizedPoint, toolState.eraser.width);
        drawingBuffer.points.push(alignedPoint.x, alignedPoint.y);
        await selectedEntity.adapter.renderer.setBuffer(drawingBuffer);
        selectedEntity.adapter.renderer.commitBuffer();
      } else if (toolState.selected === 'rect' && drawingBuffer?.type === 'rect') {
        drawingBuffer.rect.width = Math.round(normalizedPoint.x - drawingBuffer.rect.x);
        drawingBuffer.rect.height = Math.round(normalizedPoint.y - drawingBuffer.rect.y);
        await selectedEntity.adapter.renderer.setBuffer(drawingBuffer);
        selectedEntity.adapter.renderer.commitBuffer();
      }
    }

    manager.preview.tool.render();
  });

  //#region wheel
  stage.on('wheel', (e) => {
    e.evt.preventDefault();

    if (e.evt.ctrlKey || e.evt.metaKey) {
      const toolState = getToolState();
      let delta = e.evt.deltaY;
      if (toolState.invertScroll) {
        delta = -delta;
      }
      // Holding ctrl or meta while scrolling changes the brush size
      if (toolState.selected === 'brush') {
        setBrushWidth(calculateNewBrushSize(toolState.brush.width, delta));
      } else if (toolState.selected === 'eraser') {
        setEraserWidth(calculateNewBrushSize(toolState.eraser.width, delta));
      }
    } else {
      // We need the absolute cursor position - not the scaled position
      const cursorPos = stage.getPointerPosition();
      if (cursorPos) {
        // When wheeling on trackpad, e.evt.ctrlKey is true - in that case, let's reverse the direction
        const delta = e.evt.ctrlKey ? -e.evt.deltaY : e.evt.deltaY;
        const scale = manager.getStageScale() * CANVAS_SCALE_BY ** delta;
        manager.setStageScale(scale, cursorPos);
      }
    }
    manager.preview.tool.render();
  });

  //#region dragmove
  stage.on('dragmove', () => {
    $stageAttrs.set({
      x: Math.floor(stage.x()),
      y: Math.floor(stage.y()),
      width: stage.width(),
      height: stage.height(),
      scale: stage.scaleX(),
    });
  });

  //#region dragend
  stage.on('dragend', () => {
    // Stage position should always be an integer, else we get fractional pixels which are blurry
    $stageAttrs.set({
      x: Math.floor(stage.x()),
      y: Math.floor(stage.y()),
      width: stage.width(),
      height: stage.height(),
      scale: stage.scaleX(),
    });
    manager.preview.tool.render();
  });

  //#region key
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.repeat) {
      return;
    }
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
      return;
    }
    if (e.key === 'Escape') {
      // Cancel shape drawing on escape
      const selectedEntity = getSelectedEntity();
      if (selectedEntity) {
        selectedEntity.adapter.renderer.clearBuffer();
        $lastMouseDownPos.set(null);
      }
    } else if (e.key === ' ') {
      // Select the view tool on space key down
      setToolBuffer(getToolState().selected);
      setTool('view');
      $spaceKey.set(true);
      $lastCursorPos.set(null);
      $lastMouseDownPos.set(null);
    }
  };
  window.addEventListener('keydown', onKeyDown);

  const onKeyUp = (e: KeyboardEvent) => {
    if (e.repeat) {
      return;
    }
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
      return;
    }
    if (e.key === ' ') {
      // Revert the tool to the previous tool on space key up
      const toolBuffer = getToolState().selectedBuffer;
      setTool(toolBuffer ?? 'move');
      setToolBuffer(null);
      $spaceKey.set(false);
    }
    manager.preview.tool.render();
  };
  window.addEventListener('keyup', onKeyUp);

  return () => {
    stage.off('mousedown mouseup mousemove mouseenter mouseleave wheel dragend');
    window.removeEventListener('keydown', onKeyDown);
    window.removeEventListener('keyup', onKeyUp);
  };
};

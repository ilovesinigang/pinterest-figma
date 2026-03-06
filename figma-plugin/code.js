/**
 * Pinterest → Figma — Plugin code (runs in Figma sandbox)
 * Receives image data from the UI and places images in a grid on canvas.
 */

figma.showUI(__html__, { width: 420, height: 500, title: 'Pinterest → Figma' });

const COLS = 5;
const SIZE = 280;
const GAP  = 12;
const PAD  = 24;

let container = null;
let placed = 0;

figma.ui.onmessage = async (msg) => {

  // ── Start: create the container frame ──────────────────────────────────────
  if (msg.type === 'start') {
    const { total, boardName } = msg;

    container = figma.createFrame();
    container.name = `Pinterest · ${boardName}`;
    container.fills = [{ type: 'SOLID', color: { r: 0.98, g: 0.98, b: 0.98 } }];

    const rows = Math.ceil(total / COLS);
    const w = PAD + COLS * SIZE + (COLS - 1) * GAP + PAD;
    const h = PAD + rows * SIZE + (rows - 1) * GAP + PAD;
    container.resize(w, h);

    const { x, y } = figma.viewport.center;
    container.x = x - w / 2;
    container.y = y - h / 2;
    figma.currentPage.appendChild(container);

    placed = 0;
    figma.ui.postMessage({ type: 'ack' });
  }

  // ── Image: place one image into the grid ───────────────────────────────────
  if (msg.type === 'image') {
    const { bytes, index } = msg;
    const col = index % COLS;
    const row = Math.floor(index / COLS);

    const frame = figma.createFrame();
    frame.resize(SIZE, SIZE);
    frame.x = PAD + col * (SIZE + GAP);
    frame.y = PAD + row * (SIZE + GAP);
    frame.clipsContent = true;

    try {
      const image = figma.createImage(new Uint8Array(bytes));
      frame.fills = [{ type: 'IMAGE', scaleMode: 'FILL', imageHash: image.hash }];
    } catch (e) {
      frame.fills = [{ type: 'SOLID', color: { r: 0.88, g: 0.88, b: 0.88 } }];
    }

    if (container) container.appendChild(frame);
    placed++;
    figma.ui.postMessage({ type: 'ack' });
  }

  // ── Finish: zoom to fit ────────────────────────────────────────────────────
  if (msg.type === 'finish') {
    if (container) figma.viewport.scrollAndZoomIntoView([container]);
    figma.ui.postMessage({ type: 'done', count: placed });
  }

  if (msg.type === 'close') {
    figma.closePlugin();
  }
};

/**
 * Pinterest → Figma — Plugin code (runs in Figma sandbox)
 * Receives image data from the UI and places images in a grid on canvas.
 */

figma.showUI(__html__, { width: 420, height: 500, title: 'Pinterest → Figma' });

figma.ui.onmessage = async (msg) => {

  if (msg.type === 'place-images') {
    const { images, boardName } = msg;

    if (!images || images.length === 0) {
      figma.notify('No images to place.');
      figma.ui.postMessage({ type: 'done', count: 0 });
      return;
    }

    const COLS = 5;
    const SIZE = 280;  // square cells — Pinterest grid style
    const GAP  = 12;
    const PAD  = 24;

    // Container frame
    const container = figma.createFrame();
    container.name = `Pinterest · ${boardName}`;
    container.fills = [{ type: 'SOLID', color: { r: 0.98, g: 0.98, b: 0.98 } }];

    const rows = Math.ceil(images.length / COLS);
    const w = PAD + COLS * SIZE + (COLS - 1) * GAP + PAD;
    const h = PAD + rows * SIZE + (rows - 1) * GAP + PAD;
    container.resize(w, h);

    for (let i = 0; i < images.length; i++) {
      const { bytes } = images[i];
      const col = i % COLS;
      const row = Math.floor(i / COLS);

      const frame = figma.createFrame();
      frame.resize(SIZE, SIZE);
      frame.x = PAD + col * (SIZE + GAP);
      frame.y = PAD + row * (SIZE + GAP);
      frame.clipsContent = true;
      frame.fills = [];

      try {
        const image = figma.createImage(new Uint8Array(bytes));
        frame.fills = [{ type: 'IMAGE', scaleMode: 'FILL', imageHash: image.hash }];
      } catch (e) {
        // Fallback: grey placeholder if image fails
        frame.fills = [{ type: 'SOLID', color: { r: 0.88, g: 0.88, b: 0.88 } }];
      }

      container.appendChild(frame);
    }

    // Place at viewport center
    const { x, y } = figma.viewport.center;
    container.x = x - w / 2;
    container.y = y - h / 2;

    figma.currentPage.appendChild(container);
    figma.viewport.scrollAndZoomIntoView([container]);

    figma.ui.postMessage({ type: 'done', count: images.length });
  }

  if (msg.type === 'close') {
    figma.closePlugin();
  }
};

/**
 * Pinterest → Figma — Plugin code (runs in Figma sandbox)
 * Receives image data from the UI and places images in a grid on canvas.
 * Uses actual image dimensions — no cropping.
 */

figma.showUI(__html__, { width: 420, height: 500, title: 'Pinterest → Figma' });

var COLS = 5;
var COL_WIDTH = 236;
var GAP = 12;
var PAD = 24;

var container = null;
var placed = 0;
var colHeights = [];

figma.ui.onmessage = async function (msg) {

  // ── Start: create the container frame ──────────────────────────────────────
  if (msg.type === 'start') {
    var total = msg.total;
    var boardName = msg.boardName;

    container = figma.createFrame();
    container.name = 'Pinterest - ' + boardName;
    container.fills = [{ type: 'SOLID', color: { r: 0.98, g: 0.98, b: 0.98 } }];
    container.clipsContent = false;

    // Start with a reasonable size, will resize at the end
    var w = PAD + COLS * COL_WIDTH + (COLS - 1) * GAP + PAD;
    container.resize(w, 2000);

    var center = figma.viewport.center;
    container.x = center.x - w / 2;
    container.y = center.y;
    figma.currentPage.appendChild(container);

    placed = 0;
    colHeights = [];
    for (var i = 0; i < COLS; i++) colHeights.push(0);

    figma.ui.postMessage({ type: 'ack' });
  }

  // ── Image: place one image using masonry layout ────────────────────────────
  if (msg.type === 'image') {
    var bytes = msg.bytes;
    var index = msg.index;

    try {
      var imageData = new Uint8Array(bytes);
      var image = figma.createImage(imageData);
      var size = await image.getSizeAsync();

      // Scale to column width, preserve aspect ratio
      var scale = COL_WIDTH / size.width;
      var frameW = COL_WIDTH;
      var frameH = Math.round(size.height * scale);

      // Find shortest column
      var shortestCol = 0;
      for (var c = 1; c < COLS; c++) {
        if (colHeights[c] < colHeights[shortestCol]) shortestCol = c;
      }

      var frame = figma.createFrame();
      frame.resize(frameW, frameH);
      frame.x = PAD + shortestCol * (COL_WIDTH + GAP);
      frame.y = PAD + colHeights[shortestCol];
      frame.fills = [{ type: 'IMAGE', scaleMode: 'FILL', imageHash: image.hash }];

      if (container) container.appendChild(frame);
      colHeights[shortestCol] += frameH + GAP;
      placed++;
    } catch (e) {
      console.error('[plugin] image ' + index + ' FAILED:', e);
    }

    figma.ui.postMessage({ type: 'ack' });
  }

  // ── Finish: resize container and zoom to fit ──────────────────────────────
  if (msg.type === 'finish') {
    if (container) {
      // Resize container to fit tallest column
      var maxH = 0;
      for (var i = 0; i < colHeights.length; i++) {
        if (colHeights[i] > maxH) maxH = colHeights[i];
      }
      var w = PAD + COLS * COL_WIDTH + (COLS - 1) * GAP + PAD;
      container.resize(w, maxH + PAD);
      figma.viewport.scrollAndZoomIntoView([container]);
    }
    figma.ui.postMessage({ type: 'done', count: placed });
  }

  if (msg.type === 'close') {
    figma.closePlugin();
  }
};

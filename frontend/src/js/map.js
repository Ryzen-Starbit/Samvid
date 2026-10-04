import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { img } from "./api.js";

export function aoiMap(el, aois, { latestScenes = {}, onTileClick } = {}) {
  const map = L.map(el, { zoomControl: true, attributionControl: false, minZoom: 3, maxZoom: 19 });
  const groups = [];
  aois.forEach((a) => {
    if (!a.bounds) return;
    const [w, s, e, n] = a.bounds;
    const b = L.latLngBounds([s, w], [n, e]);
    groups.push(b);
    if (latestScenes[a.id]) L.imageOverlay(img(`/api/scenes/${latestScenes[a.id]}/image.png`), b, { opacity: 0.95 }).addTo(map);
    L.rectangle(b, { color: "#2c968f", weight: 1.5, fill: !latestScenes[a.id], fillOpacity: 0.08 })
      .bindTooltip(a.id, { permanent: true, direction: "top", className: "aoi-label" }).addTo(map);
  });
  if (groups.length) map.fitBounds(groups.reduce((acc, b) => acc.extend(b), L.latLngBounds(groups[0].getSouthWest(), groups[0].getNorthEast())), { padding: [20, 20] });
  else map.setView([22, 79], 5);
  return map;
}

export function sceneMap(el, aoi, sceneId) {
  const map = L.map(el, { attributionControl: false, zoomSnap: 0.25 });
  const [w, s, e, n] = aoi.bounds;
  const b = L.latLngBounds([s, w], [n, e]);
  let overlay = L.imageOverlay(img(`/api/scenes/${sceneId}/image.png`), b).addTo(map);
  map.fitBounds(b);
  const layer = L.layerGroup().addTo(map);
  return {
    map,
    setScene(id, layerName = "rgb") { overlay.setUrl(img(`/api/scenes/${id}/image.png?layer=${layerName}`)); },
    setUrl(url) { overlay.setUrl(url); },
    clear() { layer.clearLayers(); },
    box(bounds, opts = {}) {
      const [x0, y0, x1, y1] = bounds;
      return L.rectangle([[y0, x0], [y1, x1]], { color: opts.color || "#ffd60a", weight: opts.weight || 2, fill: false, ...opts }).addTo(layer);
    },
    pxBox(bbox, opts = {}) {     
      const px = (e - w) / 256, py = (n - s) / 256;
      const [x0, y0, x1, y1] = bbox;
      return L.rectangle([[n - y1 * py, w + x0 * px], [n - y0 * py, w + x1 * px]], { color: opts.color || "#ffd60a", weight: 2, fill: false, ...opts }).addTo(layer);
    },
  };
}

export function tilesMap(el, aois, tiles, { color = "#ffd60a", onClick } = {}) {
  const map = aoiMap(el, aois, {});
  const pts = [];
  tiles.forEach((t) => {
    if (!t.bounds) return;
    const [x0, y0, x1, y1] = t.bounds;
    const r = L.rectangle([[y0, x0], [y1, x1]], { color, weight: 2, fillOpacity: 0.25 }).addTo(map);
    if (t.label) r.bindTooltip(t.label);
    if (onClick) r.on("click", () => onClick(t));
    pts.push([[y0, x0], [y1, x1]]);
  });
  return map;
}

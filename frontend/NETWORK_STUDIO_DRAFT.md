# Network Studio draft format

Network Studio stores an editable schematic as a local draft. It does not send a draft to the backend or claim hydraulic validity.

## Storage

- **Save draft** writes the current project to browser `localStorage` under `hydropulse.network-studio.draft.v1`.
- **Reopen saved draft** restores that browser-local copy.
- **Export draft JSON** downloads the same draft package; **Open draft JSON** imports it.
- The package uses `schema_version: 1`. Imports with a different version or missing required sections are rejected.

## Package shape

```json
{
  "schema_version": 1,
  "project": {
    "id": "draft-mock-id",
    "name": "Untitled Water Network"
  },
  "network": {
    "network_name": "Untitled Water Network",
    "nodes": [],
    "pipes": []
  },
  "editor_layout": {
    "mode": "schematic",
    "positions": {},
    "labels": {},
    "notes": {},
    "viewport": { "x": 0, "y": 0, "zoom": 1 },
    "grid_size": 20
  }
}
```

`network.nodes` and `network.pipes` retain the HydroPulse field names. Newly created draft nodes and pipes leave unknown engineering measurements empty; the validation panel explains incomplete fields. Labels, notes, viewport, and node positions live in `editor_layout` and never overwrite `lat` or `lng`. Schematic coordinates are screen-space positions, not metres or map coordinates.

The current API contract has no project persistence endpoint. Roughness, material, tank operating levels, and server-side hydraulic validation are also outside that contract, so the editor does not invent or submit those values.

## Topology checks

The validation panel checks IDs, pipe endpoints, self/duplicate connections, required numeric fields, pipe status, isolated junctions, dead ends, and source reachability. Reachability treats pipes as undirected. These checks describe connectivity only; they do not calculate pressure, flow, or hydraulic validity.

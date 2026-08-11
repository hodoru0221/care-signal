"""Static ward layout used by the staff dashboard.

Monitoring data is stored separately from this layout so room positions can be
changed without rewriting historical observations or events.
"""

WARD_LAYOUT = {
    "id": "ward-a",
    "name": "Care Signal 병동 A",
    "rooms": [
        {"room_id": "room-01", "label": "101호", "bed_label": "침상 1", "x": 1, "y": 1, "width": 2, "height": 2},
        {"room_id": "room-02", "label": "102호", "bed_label": "침상 1", "x": 3, "y": 1, "width": 2, "height": 2},
        {"room_id": "room-03", "label": "103호", "bed_label": "침상 1", "x": 5, "y": 1, "width": 2, "height": 2},
        {"room_id": "room-04", "label": "104호", "bed_label": "침상 1", "x": 1, "y": 4, "width": 2, "height": 2},
        {"room_id": "room-05", "label": "105호", "bed_label": "침상 1", "x": 3, "y": 4, "width": 2, "height": 2},
        {"room_id": "room-06", "label": "106호", "bed_label": "침상 1", "x": 5, "y": 4, "width": 2, "height": 2},
    ],
    "stations": [
        {"id": "nurse-station", "label": "간호 스테이션", "x": 3, "y": 3, "width": 2, "height": 1}
    ],
}

WARD_ROOM_IDS = tuple(room["room_id"] for room in WARD_LAYOUT["rooms"])


def ward_map_payload(store) -> dict:
    statuses = {room["room_id"]: room for room in store.rooms()}
    rooms = [
        {**layout_room, "status": statuses[layout_room["room_id"]]}
        for layout_room in WARD_LAYOUT["rooms"]
    ]
    return {
        "id": WARD_LAYOUT["id"],
        "name": WARD_LAYOUT["name"],
        "rooms": rooms,
        "stations": WARD_LAYOUT["stations"],
    }

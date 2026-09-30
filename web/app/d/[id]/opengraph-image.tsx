import { ImageResponse } from "next/og";
import { getDetail } from "@/lib/api";

export const revalidate = 300;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "SOMETHING IS HAPPENING";

export default async function Image({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const d = await getDetail(id);
  const red = d?.level !== "orange";
  return new ImageResponse(
    (
      <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between", width: "100%", height: "100%",
        background: "#0b0d10", color: "#f1f3f5", padding: 64, borderLeft: `24px solid ${red ? "#ff4d4f" : "#ffa940"}` }}>
        <div style={{ fontSize: 34, letterSpacing: 6, color: "#8b949e" }}>SOMETHING IS HAPPENING</div>
        <div style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ fontSize: 84, fontWeight: 800 }}>{d?.entity ?? "Detection"}</div>
          <div style={{ fontSize: 120, fontWeight: 800, color: red ? "#ff4d4f" : "#ffa940" }}>
            {d ? `+${d.change_pct}%` : ""}
          </div>
        </div>
        <div style={{ fontSize: 30, color: "#8b949e" }}>
          {d ? `First detected ${new Date(d.first_detected_at).toUTCString()} · ${d.communities.length} communities` : ""}
        </div>
      </div>
    ),
    size,
  );
}

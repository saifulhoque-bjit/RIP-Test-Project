import { useState } from "react";
import { useParams } from "react-router-dom";
import { Panel } from "../Overview/Panel";
import { Pill } from "@/components/common/Pill";
import { Pagination } from "@/components/common/Pagination";
import { Timeline, type TimelineItem } from "@/components/common/Timeline";
import { useGetActivityLogsQuery } from "@/services/api/modules/activityLog";
import { formatDate } from "@/utils/formatDate";
import type { ActivityLogEntry } from "@/types";

const PAGE_SIZE = 20;

/** Icon/color node for an activity, matched by keyword against `activity_type`. */
const ACT_NODE_RULES: { test: RegExp; icon: string; bg: string }[] = [
  { test: /approve|complet|accept/, icon: "✓", bg: "bg-success" },
  { test: /sync/, icon: "⇅", bg: "bg-info" },
  { test: /export/, icon: "⬇", bg: "bg-accent" },
  { test: /override|reject|fail/, icon: "!", bg: "bg-warn" },
  { test: /generat|ingest|creat/, icon: "↑", bg: "bg-ai" },
];
const DEFAULT_ACT_NODE = { icon: "•", bg: "bg-mut" };

function getActivityNode(activityType: string) {
  const type = activityType.toLowerCase();
  return (
    ACT_NODE_RULES.find((rule) => rule.test.test(type)) ?? DEFAULT_ACT_NODE
  );
}

function toTimelineItem(entry: ActivityLogEntry): TimelineItem {
  const node = getActivityNode(entry.activity_type);
  const timestamp = formatDate(entry.created_at);
  const hasDistinctMessage = entry.message && entry.message !== entry.summary;
  const actorName = entry.actor?.name;
  const details = hasDistinctMessage ? entry.message : timestamp;

  return {
    key: entry.id,
    icon: node.icon,
    iconClassName: `${node.bg} text-white`,
    title: entry.summary || entry.message,
    subtitle: `${details}${hasDistinctMessage ? ` · ${timestamp}` : ""}${actorName ? ` · ${actorName}` : ""}`,
  };
}

export default function Activity() {
  const { id: projectId } = useParams<{ id: string }>();
  const [skip, setSkip] = useState(0);

  const { data, isFetching } = useGetActivityLogsQuery(
    { projectId: projectId ?? "", skip, limit: PAGE_SIZE },
    { skip: !projectId },
  );

  const items = data?.data.items ?? [];
  const total = data?.data.total ?? 0;
  const timelineItems = items.map(toTimelineItem);

  return (
    <div className="w-full">
      <Panel>
        {isFetching && timelineItems.length === 0 ? (
          <div className="text-[13px] text-mut">Loading activity…</div>
        ) : timelineItems.length === 0 ? (
          <div className="text-[13px] text-mut">No activity yet.</div>
        ) : (
          <Timeline items={timelineItems} />
        )}
      </Panel>

      {total > PAGE_SIZE && (
        <Pagination
          skip={skip}
          limit={PAGE_SIZE}
          total={total}
          onSkipChange={setSkip}
          itemLabel="activities"
          className="mt-2"
        />
      )}

      <div className="text-xs text-mut">
        <Pill tone="conf">Audit</Pill> Aggregates provenance already tracked
        (decided_by, versions).
      </div>
    </div>
  );
}

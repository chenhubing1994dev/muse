import type {
  BaguaTrigramItem,
  GalaxyResponse,
  KnowledgeGraphEdge,
  KnowledgeGraphNode,
  PrometheusCoreResponse,
  StarMansionItem,
} from "./src/lib/api";

export declare function polarToCartesian(cx: number, cy: number, radius: number, angleDeg: number): { x: number; y: number };
export declare function describeArcPath(cx: number, cy: number, radius: number, startDeg: number, endDeg: number): string;

export interface BaguaRingNode extends BaguaTrigramItem {
  index: number;
  angleDeg: number;
  x: number;
  y: number;
  linesTopDown: boolean[];
}

export interface StarMansionRingNode extends StarMansionItem {
  order: number;
  angleDeg: number;
  x: number;
  y: number;
  lx: number;
  ly: number;
}

export interface PrometheusStageRingNode {
  key: string;
  short: string;
  metric: number;
  angleDeg: number;
  x: number;
  y: number;
}

export declare function buildBaguaRingItems(trigrams?: BaguaTrigramItem[], cx?: number, cy?: number, radius?: number): BaguaRingNode[];
export declare function buildStarMansionRingItems(mansions?: StarMansionItem[], cx?: number, cy?: number, radius?: number): StarMansionRingNode[];
export declare function buildPrometheusStageItems(stages?: PrometheusCoreResponse["stages"] | null, cx?: number, cy?: number, radius?: number): PrometheusStageRingNode[];

export declare const KNOWLEDGE_TYPE_META: Record<string, { label: string; color: string; angleDeg: number }>;

export interface PositionedKnowledgeNode extends KnowledgeGraphNode {
  color: string;
  r: number;
  x: number;
  y: number;
}

export interface PositionedKnowledgeEdge extends KnowledgeGraphEdge {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export declare function layoutKnowledgeConstellation(
  nodes: KnowledgeGraphNode[],
  edges: KnowledgeGraphEdge[],
  width?: number,
  height?: number,
): {
  bounds: { width: number; height: number; cx: number; cy: number };
  nodes: PositionedKnowledgeNode[];
  edges: PositionedKnowledgeEdge[];
};

export declare function layoutAgentGalaxy(
  galaxy: GalaxyResponse | null,
  width?: number,
  height?: number,
): {
  center: { x: number; y: number; title: string };
  orbits: Array<{ label: string; r: number }>;
  specialists: Array<GalaxyResponse["specialist_profiles"][number] & { angleDeg: number; x: number; y: number }>;
  liveAgents: Array<GalaxyResponse["live_agents"][number] & { angleDeg: number; x: number; y: number }>;
  capabilities: Array<GalaxyResponse["capabilities"][number] & { angleDeg: number; x: number; y: number }>;
};

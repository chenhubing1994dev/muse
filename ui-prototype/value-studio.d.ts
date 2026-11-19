export interface ValueDimensionScore {
  key: string;
  label: string;
  hint: string;
  weight: number;
  score: number;
  matched: string[];
}

export interface ValueRiskFlag {
  code: string;
  label: string;
}

export interface IdeaScoreResult {
  schemaVersion: number;
  nature: string;
  label: string;
  scores: ValueDimensionScore[];
  structure: string[];
  riskFlags: ValueRiskFlag[];
  total: number;
  tier: 'S' | 'A' | 'B' | 'C';
  suggestedPath: string;
  potentialTags: string[];
  suggestedPlatforms: string[];
  monetizationNote: string;
  wordCount: number;
  empty: boolean;
}

export declare const VALUE_DIMENSIONS: Array<{
  key: string;
  label: string;
  hint: string;
  weight: number;
  keywords: string[];
}>;

export declare function scoreIdea(text: string): IdeaScoreResult;
export declare function toInspirationBrief(result: IdeaScoreResult, originalText: string): string;

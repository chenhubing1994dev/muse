import React from 'react';
import {
  AbsoluteFill,
  Easing,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  type CalculateMetadataFunction,
} from 'remotion';
import {TransitionSeries, linearTiming, springTiming} from '@remotion/transitions';
import {fade} from '@remotion/transitions/fade';
import {wipe} from '@remotion/transitions/wipe';
import {flip} from '@remotion/transitions/flip';
import {cube} from '@remotion/transitions/cube';
import {slide} from '@remotion/transitions/slide';
import {clockWipe} from '@remotion/transitions/clock-wipe';

export type FilmActMode =
  | 'hook_radar'
  | 'pain_split'
  | 'evidence_bars'
  | 'counter_balance'
  | 'evolution_loop'
  | 'action_seal';

export type FilmAct = {
  id: string;
  chapter: string;
  headline: string;
  subheadline: string;
  statValue: string;
  statLabel: string;
  citationBadge: string;
  visualMode: FilmActMode;
  accent: string;
  caption: string;
  durationInFrames: number;
};

export type ViralFilmProps = {
  title: string;
  subtitle: string;
  hookBadge: string;
  aspectRatio: '9:16' | '16:9';
  fps: number;
  transitionFrames: number;
  coverMode?: boolean;
  acts: FilmAct[];
};

const CJK_FONT =
  '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC", "Segoe UI", sans-serif';
const MONO_FONT = '"JetBrains Mono", "Cascadia Mono", Consolas, monospace';

export const defaultViralResearchFilmProps: ViralFilmProps = {
  title: '90%的人都在无效用AI',
  subtitle: 'MUSE · RESEARCH / EVOLVE / CREATE',
  hookBadge: '反证进化闭环 · 实战拆解',
  aspectRatio: '9:16',
  fps: 30,
  transitionFrames: 18,
  coverMode: false,
  acts: [
    {
      id: 'act-01',
      chapter: '01 / 3-SEC HOOK',
      headline: '90%的人用AI都在制造垃圾内容',
      subheadline: '差别不在模型贵不贵，而在有没有「反证进化闭环」',
      statValue: '90% vs 1%',
      statLabel: '单次顺从提问极易陷入「自信幻觉」与无效堆砌',
      citationBadge: 'MUSE · 知识进化拆解',
      visualMode: 'hook_radar',
      accent: '#00f0ff',
      caption: '90%的人用AI没复利？问题不在模型，而在缺了「反证闭环」',
      durationInFrames: 240,
    },
  ],
};

export const calculateViralResearchFilmMetadata: CalculateMetadataFunction<ViralFilmProps> = ({
  props,
}) => {
  const isLandscape = props.aspectRatio === '16:9';
  const width = isLandscape ? 1920 : 1080;
  const height = isLandscape ? 1080 : 1920;
  const fps = Math.max(24, Math.min(60, Math.round(props.fps || 30)));
  const acts = Array.isArray(props.acts) && props.acts.length ? props.acts : defaultViralResearchFilmProps.acts;
  const tFrames = Math.max(4, Math.min(24, Math.round(props.transitionFrames || 12)));
  const totalActFrames = acts.reduce((acc, item) => acc + Math.max(45, item.durationInFrames), 0);
  const overlap = Math.max(0, acts.length - 1) * tFrames;
  const durationInFrames = Math.max(60, totalActFrames - overlap);
  return {width, height, fps, durationInFrames};
};

const AstrolabeBackdrop: React.FC<{
  frame: number;
  width: number;
  height: number;
  accent: string;
}> = ({frame, width, height, accent}) => {
  const cx = width * 0.5;
  const cy = height * 0.44;
  const baseRadius = Math.min(width, height) * 0.42;
  const rotA = (frame * 0.38) % 360;
  const rotB = (-frame * 0.24) % 360;
  const glowX = 50 + Math.sin(frame / 36) * 7;
  const glowY = 38 + Math.cos(frame / 44) * 6;
  const pulse = 0.85 + 0.15 * Math.sin(frame / 19);

  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(circle at ${glowX}% ${glowY}%, ${accent}26 0%, #0d1c36 42%, #070e1d 100%)`,
        overflow: 'hidden',
      }}
    >
      {/* Subtle animated dot/grid shimmer so no static dark blocks form */}
      <svg width={width} height={height} style={{position: 'absolute', inset: 0, opacity: 0.42}}>
        <defs>
          <pattern id="museGrid" width="72" height="72" patternUnits="userSpaceOnUse">
            <path d="M 72 0 L 0 0 0 72" fill="none" stroke="#365783" strokeWidth="1" strokeOpacity="0.28" />
          </pattern>
        </defs>
        <rect
          width={width + 144}
          height={height + 144}
          x={-((frame * 0.7) % 72)}
          y={-((frame * 0.45) % 72)}
          fill="url(#museGrid)"
        />
        <g transform={`translate(${cx}, ${cy})`}>
          <g transform={`rotate(${rotA})`}>
            <circle r={baseRadius * 1.15} fill="none" stroke={accent} strokeOpacity="0.22" strokeWidth="1.8" strokeDasharray="10 14" />
            <circle r={baseRadius * 0.82} fill="none" stroke="#d8b46a" strokeOpacity="0.24" strokeWidth="1.2" />
            {[0, 45, 90, 135, 180, 225, 270, 315].map((deg) => {
              const rad = (deg * Math.PI) / 180;
              return (
                <line
                  key={deg}
                  x1={Math.cos(rad) * baseRadius * 0.82}
                  y1={Math.sin(rad) * baseRadius * 0.82}
                  x2={Math.cos(rad) * baseRadius * 1.15}
                  y2={Math.sin(rad) * baseRadius * 1.15}
                  stroke={accent}
                  strokeOpacity="0.26"
                  strokeWidth="1.5"
                />
              );
            })}
          </g>
          <g transform={`rotate(${rotB})`}>
            <circle r={baseRadius * 0.98} fill="none" stroke="#8cb4ff" strokeOpacity="0.18" strokeWidth="1.5" strokeDasharray="4 18" />
          </g>
          <circle r={baseRadius * 0.38 * pulse} fill={accent} fillOpacity="0.06" />
        </g>
      </svg>

      {/* Moving celestial embers */}
      {Array.from({length: 20}, (_, idx) => {
        const yFrac = (((idx * 37 + frame * (0.42 + (idx % 4) * 0.11)) % 115) / 115);
        const xFrac = ((idx * 53 + Math.sin((frame + idx * 17) / 28) * 14 + 100) % 100) / 100;
        const size = 3 + (idx % 4) * 1.6;
        return (
          <div
            key={'spark-' + idx}
            style={{
              position: 'absolute',
              left: `${xFrac * 100}%`,
              top: `${(1 - yFrac) * 100}%`,
              width: size,
              height: size,
              borderRadius: '50%',
              backgroundColor: idx % 3 === 0 ? '#d8b46a' : accent,
              boxShadow: `0 0 ${size * 3}px ${accent}`,
              opacity: 0.28 + 0.45 * Math.abs(Math.sin((frame + idx * 19) / 21)),
            }}
          />
        );
      })}
    </AbsoluteFill>
  );
};

const ActDataStage: React.FC<{
  act: FilmAct;
  frame: number;
  fps: number;
  isLandscape: boolean;
}> = ({act, frame, fps, isLandscape}) => {
  const enter = spring({frame, fps, config: {damping: 15, stiffness: 110, mass: 0.75}});
  const cardBg = 'rgba(10, 21, 42, 0.84)';
  const borderCol = `${act.accent}55`;
  const sweepDeg = (frame * 4.2) % 360;
  const barProg = (delay: number, target: number) =>
    interpolate(frame, [delay, delay + 26], [0, target], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
      easing: Easing.out(Easing.cubic),
    });

  if (act.visualMode === 'hook_radar') {
    const ringScale = 0.92 + 0.08 * Math.sin(frame / 10);
    return (
      <div
        style={{
          width: '100%',
          height: '100%',
          background: cardBg,
          border: `2px solid ${borderCol}`,
          borderRadius: 28,
          padding: isLandscape ? 36 : 30,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          position: 'relative',
          boxShadow: `0 20px 60px rgba(0,0,0,0.55), inset 0 0 36px ${act.accent}1a`,
        }}
      >
        <div
          style={{
            position: 'relative',
            width: isLandscape ? 350 : 410,
            height: isLandscape ? 350 : 410,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transform: `scale(${ringScale})`,
          }}
        >
          <svg width="100%" height="100%" viewBox="0 0 260 260" style={{position: 'absolute', inset: 0}}>
            <circle cx="130" cy="130" r="118" fill="none" stroke={act.accent} strokeOpacity="0.28" strokeWidth="2.2" />
            <circle cx="130" cy="130" r="90" fill="none" stroke={act.accent} strokeOpacity="0.48" strokeWidth="2" strokeDasharray="10 7" />
            <g transform={`rotate(${sweepDeg} 130 130)`}>
              <path d="M130,68 L130,12 A118,118 0 0,1 214,48 L174,88 A62,62 0 0,0 130,68 Z" fill={act.accent} fillOpacity="0.22" />
              <line x1="130" y1="68" x2="130" y2="12" stroke={act.accent} strokeWidth="4" strokeLinecap="round" />
            </g>
            <circle cx="130" cy="130" r="64" fill="#071326" stroke={act.accent} strokeWidth="2.8" />
          </svg>
          <div style={{textAlign: 'center', zIndex: 2}}>
            <div style={{fontSize: isLandscape ? 42 : 46, fontWeight: 900, color: '#ffffff', fontFamily: MONO_FONT}}>
              {act.statValue}
            </div>
            <div style={{fontSize: 20, color: act.accent, fontWeight: 800, marginTop: 6, fontFamily: CJK_FONT}}>
              效率断层对比
            </div>
          </div>
        </div>
        <div
          style={{
            marginTop: 22,
            padding: '12px 22px',
            borderRadius: 14,
            background: `${act.accent}1a`,
            border: `1px solid ${act.accent}66`,
            fontSize: isLandscape ? 23 : 22,
            color: '#e8f4ff',
            fontWeight: 600,
            textAlign: 'center',
            fontFamily: CJK_FONT,
          }}
        >
          {act.statLabel}
        </div>
      </div>
    );
  }

  if (act.visualMode === 'pain_split') {
    return (
      <div style={{width: '100%', height: '100%', display: 'flex', flexDirection: 'column', gap: 18, justifyContent: 'center'}}>
        <div
          style={{
            background: 'rgba(65, 16, 30, 0.85)',
            border: '2px solid rgba(255, 77, 109, 0.72)',
            borderRadius: 22,
            padding: isLandscape ? 28 : 24,
            transform: `translateX(${(1 - enter) * -32}px)`,
          }}
        >
          <div style={{fontSize: 22, color: '#ff8da1', fontWeight: 800, marginBottom: 8, fontFamily: CJK_FONT}}>
            ❌ 传统单次提问 · 高风险幻觉区
          </div>
          <div style={{fontSize: isLandscape ? 26 : 24, color: '#fff3f5', fontWeight: 700, lineHeight: 1.4, fontFamily: CJK_FONT}}>
            语气极其自信 · 只看顺从答案 · 缺来源核验
          </div>
        </div>
        <div
          style={{
            background: 'rgba(9, 44, 54, 0.88)',
            border: '2px solid rgba(0, 240, 255, 0.78)',
            borderRadius: 22,
            padding: isLandscape ? 28 : 24,
            transform: `translateX(${(1 - enter) * 32}px)`,
          }}
        >
          <div style={{fontSize: 22, color: '#5ef6ff', fontWeight: 800, marginBottom: 8, fontFamily: CJK_FONT}}>
            ✅ Muse 双路反证 · 确定性护栏
          </div>
          <div style={{fontSize: isLandscape ? 26 : 24, color: '#eefcff', fontWeight: 700, lineHeight: 1.4, fontFamily: CJK_FONT}}>
            拆解主张 + 原文快照 SHA-256 + 主动找反证
          </div>
        </div>
      </div>
    );
  }

  if (act.visualMode === 'evidence_bars') {
    const metrics = [
      {label: '支持来源深度检索 [S1]', pct: 92, color: '#38ef7d'},
      {label: '反面来源与局限排查 [S2]', pct: 86, color: '#d8b46a'},
      {label: '原文快照 SHA-256 存证', pct: 100, color: '#00f0ff'},
      {label: '跨独立域名三角校验', pct: 89, color: '#9d7cff'},
    ];
    return (
      <div
        style={{
          width: '100%',
          background: cardBg,
          border: `2px solid ${borderCol}`,
          borderRadius: 26,
          padding: isLandscape ? 32 : 26,
          display: 'flex',
          flexDirection: 'column',
          gap: 20,
        }}
      >
        <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'baseline'}}>
          <span style={{fontSize: 24, fontWeight: 800, color: '#e9fff4', fontFamily: CJK_FONT}}>
            双路证据覆盖仪表盘
          </span>
          <span style={{fontSize: 28, fontWeight: 900, color: act.accent, fontFamily: MONO_FONT}}>{act.statValue}</span>
        </div>
        {metrics.map((m, i) => {
          const val = barProg(i * 6, m.pct);
          return (
            <div key={m.label} style={{display: 'flex', flexDirection: 'column', gap: 8}}>
              <div style={{display: 'flex', justifyContent: 'space-between', fontSize: 20, color: '#dbe9ff', fontFamily: CJK_FONT}}>
                <span>{m.label}</span>
                <span style={{fontFamily: MONO_FONT, color: m.color, fontWeight: 700}}>{Math.round(val)}%</span>
              </div>
              <div style={{height: 14, borderRadius: 999, background: 'rgba(255,255,255,0.09)', overflow: 'hidden'}}>
                <div style={{width: `${val}%`, height: '100%', borderRadius: 999, background: m.color}} />
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  if (act.visualMode === 'counter_balance') {
    const tilt = Math.sin(frame / 18) * 3.5;
    return (
      <div
        style={{
          width: '100%',
          background: cardBg,
          border: `2px solid ${borderCol}`,
          borderRadius: 26,
          padding: isLandscape ? 32 : 26,
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
          alignItems: 'center',
        }}
      >
        <div style={{fontSize: 24, fontWeight: 800, color: '#fce8b8', fontFamily: CJK_FONT}}>
          证据天平：支持视角 ⚖️ 反面边界
        </div>
        <div
          style={{
            width: '92%',
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 16,
            transform: `rotate(${tilt * 0.45}deg)`,
          }}
        >
          <div style={{padding: 20, borderRadius: 18, background: 'rgba(56,239,125,0.12)', border: '1px solid rgba(56,239,125,0.5)'}}>
            <div style={{fontSize: 20, fontWeight: 800, color: '#63ffa6', fontFamily: CJK_FONT}}>正向可行线索</div>
            <div style={{fontSize: 18, color: '#e6fff1', marginTop: 8, lineHeight: 1.45, fontFamily: CJK_FONT}}>
              交互结构化与动态视觉可显著提升核心概念保持度
            </div>
          </div>
          <div style={{padding: 20, borderRadius: 18, background: 'rgba(216,180,106,0.15)', border: '1px solid rgba(216,180,106,0.65)'}}>
            <div style={{fontSize: 20, fontWeight: 800, color: '#ffd98a', fontFamily: CJK_FONT}}>反证与失效边界</div>
            <div style={{fontSize: 18, color: '#fff8e7', marginTop: 8, lineHeight: 1.45, fontFamily: CJK_FONT}}>
              过密信息造成认知超载；无来源引用不可外推结论
            </div>
          </div>
        </div>
        <div style={{fontSize: 20, color: '#d9e7ff', textAlign: 'center', fontFamily: CJK_FONT}}>{act.statLabel}</div>
      </div>
    );
  }

  if (act.visualMode === 'evolution_loop') {
    const steps = ['01 灵感池', '02 正反深研', '03 升级提案(人审)', '04 1080P交付'];
    const activeIdx = Math.floor((frame / 28) % 4);
    return (
      <div
        style={{
          width: '100%',
          background: cardBg,
          border: `2px solid ${borderCol}`,
          borderRadius: 26,
          padding: isLandscape ? 30 : 24,
          display: 'flex',
          flexDirection: 'column',
          gap: 18,
        }}
      >
        <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
          <span style={{fontSize: 23, fontWeight: 800, color: '#e9dcff', fontFamily: CJK_FONT}}>
            Muse 自我进化闭环回路
          </span>
          <span style={{fontSize: 24, fontWeight: 900, color: '#c4b0ff', fontFamily: MONO_FONT}}>{act.statValue}</span>
        </div>
        <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14}}>
          {steps.map((step, idx) => {
            const highlighted = idx === activeIdx;
            return (
              <div
                key={step}
                style={{
                  padding: '18px 16px',
                  borderRadius: 18,
                  background: highlighted ? 'rgba(157, 124, 255, 0.28)' : 'rgba(255,255,255,0.05)',
                  border: `2px solid ${highlighted ? '#c4b0ff' : 'rgba(255,255,255,0.14)'}`,
                  fontSize: isLandscape ? 23 : 21,
                  fontWeight: 800,
                  color: highlighted ? '#ffffff' : '#bfd0ea',
                  textAlign: 'center',
                  transform: `scale(${highlighted ? 1.03 : 1})`,
                  fontFamily: CJK_FONT,
                }}
              >
                {step}
              </div>
            );
          })}
        </div>
        <div style={{fontSize: 19, color: '#d4e2fa', textAlign: 'center', fontFamily: CJK_FONT}}>
          {act.statLabel}
        </div>
      </div>
    );
  }

  // action_seal
  const todos = [
    '① 记灵感：打标保存至本地账本',
    '② 查反证：双检索过滤幻觉盲区',
    '③ 做交付：一键生成横竖双画幅',
  ];
  return (
    <div
      style={{
        width: '100%',
        background: cardBg,
        border: `2px solid ${borderCol}`,
        borderRadius: 26,
        padding: isLandscape ? 30 : 24,
        display: 'flex',
        flexDirection: 'column',
        gap: 14,
      }}
    >
      <div style={{fontSize: 24, fontWeight: 900, color: act.accent, fontFamily: CJK_FONT}}>
        PROMETHEUS · 立即可执行三步清单
      </div>
      {todos.map((item, idx) => {
        const op = interpolate(frame, [idx * 10, idx * 10 + 14], [0, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });
        return (
          <div
            key={item}
            style={{
              opacity: op,
              padding: '14px 18px',
              borderRadius: 16,
              background: 'rgba(0, 240, 255, 0.11)',
              border: '1px solid rgba(0, 240, 255, 0.42)',
              fontSize: isLandscape ? 23 : 22,
              fontWeight: 700,
              color: '#f4fbff',
              fontFamily: CJK_FONT,
            }}
          >
            {item}
          </div>
        );
      })}
    </div>
  );
};

const ActScene: React.FC<{
  act: FilmAct;
  actIndex: number;
  totalActs: number;
  aspectRatio: '9:16' | '16:9';
  subtitle: string;
  coverMode?: boolean;
}> = ({act, actIndex, totalActs, aspectRatio, subtitle, coverMode}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const isLandscape = aspectRatio === '16:9';
  const enter = spring({frame, fps, config: {damping: 16, stiffness: 105, mass: 0.78}});
  const headY = interpolate(enter, [0, 1], [30, 0]);
  const progressPct = ((actIndex + Math.min(1, frame / Math.max(1, act.durationInFrames))) / totalActs) * 100;

  return (
    <AbsoluteFill style={{color: '#f4f8ff', fontFamily: CJK_FONT}}>
      <AstrolabeBackdrop frame={frame} width={width} height={height} accent={act.accent} />

      {/* Top HUD Progress & Chapter Pill */}
      <div
        style={{
          position: 'absolute',
          top: isLandscape ? 42 : 96,
          left: isLandscape ? 76 : 64,
          right: isLandscape ? 76 : 112,
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
        }}
      >
        <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
          <span
            style={{
              padding: '8px 18px',
              borderRadius: 999,
              background: `${act.accent}22`,
              border: `1.5px solid ${act.accent}`,
              color: act.accent,
              fontSize: isLandscape ? 22 : 21,
              fontWeight: 800,
              letterSpacing: 1.5,
              fontFamily: MONO_FONT,
            }}
          >
            {act.chapter}
          </span>
          <span style={{fontSize: isLandscape ? 20 : 19, color: '#9ab6db', fontFamily: MONO_FONT}}>
            {subtitle}
          </span>
        </div>
        <div style={{height: 6, borderRadius: 999, background: 'rgba(255,255,255,0.12)', overflow: 'hidden'}}>
          <div
            style={{
              width: `${progressPct}%`,
              height: '100%',
              background: `linear-gradient(90deg, ${act.accent}, #d8b46a)`,
            }}
          />
        </div>
      </div>

      {/* Main Content Stage (Portrait Stacked Safe-Zone OR Landscape Split Keynote) */}
      {isLandscape ? (
        <div
          style={{
            position: 'absolute',
            top: 155,
            left: 76,
            right: 76,
            bottom: 205,
            display: 'grid',
            gridTemplateColumns: '1.05fr 0.95fr',
            gap: 48,
            alignItems: 'center',
          }}
        >
          <div style={{transform: `translateY(${headY}px)`}}>
            <div
              style={{
                display: 'inline-block',
                padding: '8px 18px',
                borderRadius: 12,
                background: 'rgba(216, 180, 106, 0.16)',
                border: '1px solid rgba(216, 180, 106, 0.55)',
                color: '#ffd98a',
                fontSize: 22,
                fontWeight: 700,
                marginBottom: 20,
              }}
            >
              {act.citationBadge}
            </div>
            <h1 style={{margin: 0, fontSize: 66, fontWeight: 900, lineHeight: 1.2, letterSpacing: -0.5, color: '#ffffff'}}>
              {act.headline}
            </h1>
            <p style={{margin: '24px 0 0', fontSize: 32, lineHeight: 1.45, color: '#c6daf6', fontWeight: 600}}>
              {act.subheadline}
            </p>
          </div>
          <ActDataStage act={act} frame={frame} fps={fps} isLandscape={true} />
        </div>
      ) : (
        <div
          style={{
            position: 'absolute',
            top: 210,
            left: 58,
            right: 118,
            bottom: coverMode ? 240 : 410,
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'space-between',
            gap: 24,
          }}
        >
          <div style={{transform: `translateY(${headY}px)`}}>
            <div
              style={{
                display: 'inline-block',
                padding: '8px 16px',
                borderRadius: 12,
                background: 'rgba(216, 180, 106, 0.18)',
                border: '1px solid rgba(216, 180, 106, 0.6)',
                color: '#ffe099',
                fontSize: 22,
                fontWeight: 700,
                marginBottom: 18,
              }}
            >
              {act.citationBadge}
            </div>
            <h1 style={{margin: 0, fontSize: coverMode ? 76 : 64, fontWeight: 900, lineHeight: 1.18, color: '#ffffff'}}>
              {act.headline}
            </h1>
            <p style={{margin: '18px 0 0', fontSize: 30, lineHeight: 1.42, color: '#c8dcf8', fontWeight: 600}}>
              {act.subheadline}
            </p>
          </div>
          <div style={{flex: 1, display: 'flex', alignItems: 'center'}}>
            <ActDataStage act={act} frame={frame} fps={fps} isLandscape={false} />
          </div>
        </div>
      )}

      {/* Burned-In High-Contrast Caption Pill (Omitted in Cover Thumbnail Mode) */}
      {!coverMode ? (
        <div
          style={{
            position: 'absolute',
            bottom: isLandscape ? 54 : 220,
            left: isLandscape ? 140 : 58,
            right: isLandscape ? 140 : 118,
            display: 'flex',
            justifyContent: 'center',
          }}
        >
          <div
            style={{
              padding: isLandscape ? '18px 36px' : '18px 28px',
              borderRadius: 20,
              background: 'rgba(5, 11, 24, 0.88)',
              border: `1.5px solid ${act.accent}66`,
              boxShadow: '0 10px 30px rgba(0, 0, 0, 0.6)',
              fontSize: isLandscape ? 34 : 34,
              fontWeight: 800,
              lineHeight: 1.38,
              color: '#ffffff',
              textAlign: 'center',
            }}
          >
            {act.caption}
          </div>
        </div>
      ) : (
        <div
          style={{
            position: 'absolute',
            bottom: isLandscape ? 54 : 110,
            left: isLandscape ? 76 : 58,
            right: isLandscape ? 76 : 58,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 20,
            padding: '20px 30px',
            borderRadius: 20,
            background: `${act.accent}22`,
            border: `2px solid ${act.accent}`,
          }}
        >
          <span style={{fontSize: 28, fontWeight: 900, color: '#ffffff', lineHeight: 1.35}}>{act.caption}</span>
          <span style={{fontSize: 22, fontWeight: 900, color: act.accent, fontFamily: MONO_FONT, whiteSpace: 'nowrap', flexShrink: 0}}>MUSE STUDIO</span>
        </div>
      )}
    </AbsoluteFill>
  );
};

export const MuseViralResearchFilm: React.FC<ViralFilmProps> = (props) => {
  const acts = Array.isArray(props.acts) && props.acts.length ? props.acts : defaultViralResearchFilmProps.acts;
  const tFrames = Math.max(4, Math.min(24, Math.round(props.transitionFrames || 12)));
  const children: React.ReactNode[] = [];

  acts.forEach((act, idx) => {
    const dur = Math.max(45, Math.round(act.durationInFrames));
    children.push(
      <TransitionSeries.Sequence key={'act-' + act.id} durationInFrames={dur}>
        <ActScene
          act={act}
          actIndex={idx}
          totalActs={acts.length}
          aspectRatio={props.aspectRatio}
          subtitle={props.hookBadge || props.subtitle}
          coverMode={props.coverMode}
        />
      </TransitionSeries.Sequence>,
    );
    if (idx < acts.length - 1) {
      // Cinematic transition choreography: each cut gets a distinct flourish.
      const canvasW = props.aspectRatio === '16:9' ? 1920 : 1080;
      const canvasH = props.aspectRatio === '16:9' ? 1080 : 1920;
      const linear = linearTiming({durationInFrames: tFrames});
      const spring = springTiming({durationInFrames: tFrames + 4, config: {damping: 22}});
      const timing = idx % 2 === 0 ? spring : linear;
      const slot = idx % 6;
      let transition: React.ReactNode;
      if (slot === 0) {
        transition = <TransitionSeries.Transition key={'trans-' + idx} presentation={flip({direction: 'from-right', perspective: 1200})} timing={timing} />;
      } else if (slot === 1) {
        transition = <TransitionSeries.Transition key={'trans-' + idx} presentation={cube({direction: 'from-bottom', perspective: 1200})} timing={timing} />;
      } else if (slot === 2) {
        transition = <TransitionSeries.Transition key={'trans-' + idx} presentation={clockWipe({width: canvasW, height: canvasH})} timing={timing} />;
      } else if (slot === 3) {
        transition = <TransitionSeries.Transition key={'trans-' + idx} presentation={slide({direction: 'from-left'})} timing={timing} />;
      } else if (slot === 4) {
        transition = <TransitionSeries.Transition key={'trans-' + idx} presentation={wipe({direction: 'from-top'})} timing={timing} />;
      } else {
        transition = <TransitionSeries.Transition key={'trans-' + idx} presentation={flip({direction: 'from-bottom', perspective: 1200})} timing={timing} />;
      }
      children.push(transition);
    }
  });

  return <TransitionSeries>{children}</TransitionSeries>;
};

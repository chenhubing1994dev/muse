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

export type PixelPalette = 'cga' | 'ega' | 'hk-neon';

export type PixelOverlayProps = {
  title: string;
  kicker: string;
  palette: PixelPalette;
  pixelSize: number;
  density: number;
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
};

type PaletteDef = {bg: string; ink: string; colors: string[]};

const PALETTES: Record<PixelPalette, PaletteDef> = {
  cga: {
    bg: '#000000',
    ink: '#ffffff',
    colors: ['#55ffff', '#ff55ff', '#ffffff', '#55ff55'],
  },
  ega: {
    bg: '#000000',
    ink: '#ffff55',
    colors: [
      '#0000aa', '#00aa00', '#00aaaa', '#aa0000', '#aa00aa',
      '#aa5500', '#aaaaaa', '#5555ff', '#55ff55', '#55ffff',
      '#ff5555', '#ff55ff', '#ffff55',
    ],
  },
  'hk-neon': {
    bg: '#0b0b1a',
    ink: '#ffe600',
    colors: ['#ff2d95', '#00e5ff', '#ffe600', '#7a00ff', '#ff6b35'],
  },
};

export const defaultPixelOverlayProps: PixelOverlayProps = {
  title: '像素灵感引擎',
  kicker: 'MUSE / 8-BIT IGNITION',
  palette: 'hk-neon',
  pixelSize: 18,
  density: 96,
  width: 1080,
  height: 1920,
  fps: 30,
  durationInFrames: 180,
};

export const calculatePixelOverlayMetadata: CalculateMetadataFunction<PixelOverlayProps> = ({props}) => {
  const width = Math.max(64, Math.min(3840, Math.round(props.width || 1080)));
  const height = Math.max(64, Math.min(3840, Math.round(props.height || 1920)));
  const fps = Math.max(24, Math.min(60, Math.round(props.fps || 30)));
  const durationInFrames = Math.max(30, Math.min(1200, Math.round(props.durationInFrames || 180)));
  return {width, height, fps, durationInFrames};
};

// Deterministic hash-based pseudo-random from an integer index.
const hash01 = (index: number, salt = 0): number => {
  const v = Math.sin(index * 127.1 + salt * 311.7) * 43758.5453123;
  return v - Math.floor(v);
};

const PixelWipe: React.FC<{
  width: number;
  height: number;
  pixelSize: number;
  colors: string[];
  progress: number;
  opacity: number;
}> = ({width, height, pixelSize, colors, progress, opacity}) => {
  const cols = Math.max(6, Math.round(width / (pixelSize * 4)));
  const rows = Math.max(10, Math.round(height / (pixelSize * 4)));
  const cw = width / cols;
  const ch = height / rows;
  const cells: React.ReactNode[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const idx = r * cols + c;
      const delay = (c / cols) * 0.42 + hash01(idx, 1) * 0.34;
      const a = interpolate(progress, [delay, delay + 0.26], [0, 1], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
        easing: Easing.out(Easing.quad),
      });
      if (a <= 0) continue;
      cells.push(
        <div
          key={'pw-' + idx}
          style={{
            position: 'absolute',
            left: c * cw,
            top: r * ch,
            width: cw + 1,
            height: ch + 1,
            backgroundColor: colors[idx % colors.length],
            opacity: a * opacity,
            transform: `scale(${0.3 + 0.7 * a})`,
          }}
        />,
      );
    }
  }
  return <AbsoluteFill style={{opacity}}>{cells}</AbsoluteFill>;
};

const PixelGather: React.FC<{
  width: number;
  height: number;
  density: number;
  pixelSize: number;
  colors: string[];
  progress: number;
  opacity: number;
}> = ({width, height, density, pixelSize, colors, progress, opacity}) => {
  const cols = Math.max(2, Math.ceil(Math.sqrt(density)));
  const rows = Math.max(2, Math.ceil(density / cols));
  const parts: React.ReactNode[] = [];
  for (let i = 0; i < density; i++) {
    const sx = hash01(i, 11) * width;
    const sy = hash01(i, 12) * height;
    const gc = i % cols;
    const gr = Math.floor(i / cols) % rows;
    const tx = width / 2 + (gc - (cols - 1) / 2) * pixelSize * 1.25;
    const ty = height / 2 + (gr - (rows - 1) / 2) * pixelSize * 1.25;
    const delay = hash01(i, 13) * 0.32;
    const p = interpolate(progress, [delay, delay + 0.52], [0, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
      easing: Easing.inOut(Easing.cubic),
    });
    const x = sx + (tx - sx) * p + Math.sin(progress * 22 + i) * 10 * (1 - p);
    const y = sy + (ty - sy) * p + Math.cos(progress * 19 + i) * 10 * (1 - p);
    const size = pixelSize * (0.45 + hash01(i, 14) * 0.8);
    parts.push(
      <div
        key={'pg-' + i}
        style={{
          position: 'absolute',
          left: x,
          top: y,
          width: size,
          height: size,
          backgroundColor: colors[i % colors.length],
          opacity: opacity * (0.3 + 0.7 * p),
          transform: `rotate(${Math.floor(p * 4) * 90}deg)`,
        }}
      />,
    );
  }
  return <AbsoluteFill style={{opacity}}>{parts}</AbsoluteFill>;
};

const PixelCard: React.FC<{
  title: string;
  kicker: string;
  ink: string;
  colors: string[];
  bg: string;
  pixelSize: number;
  opacity: number;
  localFrame: number;
  fps: number;
}> = ({title, kicker, ink, colors, bg, pixelSize, opacity, localFrame, fps}) => {
  const enter = spring({frame: localFrame, fps, config: {damping: 16, stiffness: 110, mass: 0.7}});
  const lift = interpolate(enter, [0, 1], [60, 0]);
  const bar = Math.max(6, Math.round(pixelSize * 0.55));
  return (
    <AbsoluteFill style={{opacity, justifyContent: 'center', alignItems: 'center'}}>
      <div
        style={{
          width: '84%',
          padding: pixelSize * 2.5,
          backgroundColor: 'rgba(6, 8, 20, 0.9)',
          border: `${bar}px solid ${colors[0]}`,
          boxShadow: `0 0 0 ${bar}px ${bg}, ${bar * 3}px ${bar * 3}px 0 ${colors[1]}44`,
          transform: `translateY(${lift}px) scale(${interpolate(enter, [0, 1], [0.96, 1])})`,
          textAlign: 'center',
          fontFamily: '"JetBrains Mono", "Cascadia Mono", Consolas, monospace',
        }}
      >
        <div style={{color: colors[1], fontSize: Math.max(16, pixelSize * 1.7), letterSpacing: 4, marginBottom: pixelSize * 1.6}}>
          {kicker}
        </div>
        <div
          style={{
            color: ink,
            fontSize: Math.max(28, pixelSize * 4.4),
            fontWeight: 900,
            lineHeight: 1.24,
            textShadow: `0 0 ${pixelSize * 2}px ${colors[0]}`,
          }}
        >
          {title}
        </div>
        <div style={{display: 'flex', gap: bar * 2, marginTop: pixelSize * 2.2, justifyContent: 'center'}}>
          {colors.slice(0, 5).map((c, i) => (
            <div key={'cb-' + i} style={{width: bar * 3, height: bar * 3, backgroundColor: c}} />
          ))}
        </div>
      </div>
    </AbsoluteFill>
  );
};

export const PixelOverlay: React.FC<PixelOverlayProps> = (props) => {
  const frame = useCurrentFrame();
  const {width, height, fps, durationInFrames} = useVideoConfig();
  const palette = PALETTES[props.palette] ?? PALETTES['hk-neon'];
  const pixelSize = Math.max(4, Math.min(64, Math.round(props.pixelSize || 18)));
  const density = Math.max(8, Math.min(400, Math.round(props.density || 96)));
  const t1 = Math.round(durationInFrames * 0.34);
  const t2 = Math.round(durationInFrames * 0.68);
  const wipeOpacity = interpolate(frame, [-8, 0, t1 - 10, t1 + 4], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const gatherOpacity = interpolate(frame, [t1 - 8, t1 + 8, t2 - 8, t2 + 6], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const cardOpacity = interpolate(frame, [t2 - 6, t2 + 12, durationInFrames], [0, 1, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <AbsoluteFill style={{backgroundColor: palette.bg, overflow: 'hidden'}}>
      <PixelWipe
        width={width}
        height={height}
        pixelSize={pixelSize}
        colors={palette.colors}
        progress={interpolate(frame, [0, Math.max(1, t1)], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})}
        opacity={wipeOpacity}
      />
      <PixelGather
        width={width}
        height={height}
        density={density}
        pixelSize={pixelSize}
        colors={palette.colors}
        progress={interpolate(frame, [Math.max(0, t1 - 8), Math.max(1, t2)], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})}
        opacity={gatherOpacity}
      />
      <PixelCard
        title={props.title}
        kicker={props.kicker}
        ink={palette.ink}
        colors={palette.colors}
        bg={palette.bg}
        pixelSize={pixelSize}
        opacity={cardOpacity}
        localFrame={frame - t2}
        fps={fps}
      />
      <AbsoluteFill
        style={{
          pointerEvents: 'none',
          background: 'repeating-linear-gradient(0deg, rgba(0,0,0,0.18) 0px, rgba(0,0,0,0.18) 2px, transparent 2px, transparent 5px)',
          opacity: 0.35,
        }}
      />
    </AbsoluteFill>
  );
};

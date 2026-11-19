import React from 'react';
import {Composition} from 'remotion';
import {MuseBrandMotion, calculateBrandMotionMetadata, defaultBrandMotionProps} from './scenes/MuseBrandMotion';
import {MuseBumper, calculateMuseBumperMetadata, defaultMuseBumperProps} from './scenes/MuseBumper';
import {MuseSceneTimeline, calculateMuseSceneTimelineMetadata, defaultMuseSceneTimelineProps} from './scenes/MuseSceneTimeline';
import {
  MuseViralResearchFilm,
  calculateViralResearchFilmMetadata,
  defaultViralResearchFilmProps,
} from './scenes/MuseViralResearchFilm';
import {PixelOverlay, calculatePixelOverlayMetadata, defaultPixelOverlayProps} from './scenes/PixelOverlay';
import {HKRetroStyle, calculateHKRetroStyleMetadata, defaultHKRetroStyleProps} from './scenes/HKRetroStyle';
import {CosmicVista, calculateCosmicVistaMetadata, defaultCosmicVistaProps} from './scenes/CosmicVista';
import {CharacterMotion, calculateCharacterMotionMetadata, defaultCharacterMotionProps} from './scenes/CharacterMotion';
import {
  NarrationSubtitle,
  calculateNarrationSubtitleMetadata,
  defaultNarrationSubtitleProps,
} from './scenes/NarrationSubtitle';

export const Root: React.FC = () => (
  <>
    <Composition
      id="MuseBrandMotion"
      component={MuseBrandMotion}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={204}
      defaultProps={defaultBrandMotionProps}
      calculateMetadata={calculateBrandMotionMetadata}
    />
    <Composition
      id="MuseBumper"
      component={MuseBumper}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={60}
      defaultProps={defaultMuseBumperProps}
      calculateMetadata={calculateMuseBumperMetadata}
    />
    <Composition
      id="MuseSceneTimeline"
      component={MuseSceneTimeline}
      width={1920}
      height={1080}
      fps={30}
      durationInFrames={60}
      defaultProps={defaultMuseSceneTimelineProps}
      calculateMetadata={calculateMuseSceneTimelineMetadata}
    />
    <Composition
      id="MuseViralResearchFilm"
      component={MuseViralResearchFilm}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={240}
      defaultProps={defaultViralResearchFilmProps}
      calculateMetadata={calculateViralResearchFilmMetadata}
    />
    <Composition
      id="PixelOverlay"
      component={PixelOverlay}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={180}
      defaultProps={defaultPixelOverlayProps}
      calculateMetadata={calculatePixelOverlayMetadata}
    />
    <Composition
      id="HKRetroStyle"
      component={HKRetroStyle}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={180}
      defaultProps={defaultHKRetroStyleProps}
      calculateMetadata={calculateHKRetroStyleMetadata}
    />
    <Composition
      id="CosmicVista"
      component={CosmicVista}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={180}
      defaultProps={defaultCosmicVistaProps}
      calculateMetadata={calculateCosmicVistaMetadata}
    />
    <Composition
      id="CharacterMotion"
      component={CharacterMotion}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={180}
      defaultProps={defaultCharacterMotionProps}
      calculateMetadata={calculateCharacterMotionMetadata}
    />
    <Composition
      id="NarrationSubtitle"
      component={NarrationSubtitle}
      width={1080}
      height={1920}
      fps={30}
      durationInFrames={180}
      defaultProps={defaultNarrationSubtitleProps}
      calculateMetadata={calculateNarrationSubtitleMetadata}
    />
  </>
);

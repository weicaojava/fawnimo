/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * The independently downloadable PCM mix accompanies the usual video and subtitle files.
 */
export type SoundExportFile = {
    kind: 'video' | 'mixdown' | 'subtitles_srt' | 'subtitles_vtt' | 'package';
    name: string;
    sha256: string;
    size: number;
    content_url: string;
};


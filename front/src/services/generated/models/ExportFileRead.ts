/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A verified ready artifact can only be downloaded through an owned content route.
 */
export type ExportFileRead = {
    kind: 'video' | 'subtitles_srt' | 'subtitles_vtt' | 'package';
    name: string;
    sha256: string;
    size: number;
    content_url: string;
};


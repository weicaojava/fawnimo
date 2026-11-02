/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { LocalSpeechCatalog } from './LocalSpeechCatalog';
import type { LocalSpeechRead } from './LocalSpeechRead';
/**
 * Current voice availability and bounded actual chapter history.
 */
export type LocalSpeechHistory = {
    catalog: LocalSpeechCatalog;
    jobs: Array<LocalSpeechRead>;
};


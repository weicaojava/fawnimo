/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SoundExportRead } from './SoundExportRead';
/**
 * Bounded sound export history and current local renderer availability.
 */
export type SoundExportCatalog = {
    enabled: boolean;
    reason: string;
    exports: Array<SoundExportRead>;
};


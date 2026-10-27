/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ExportRead } from './ExportRead';
/**
 * Local tool availability is independent from model-account readiness.
 */
export type ExportCatalog = {
    enabled: boolean;
    reason: string;
    exports: Array<ExportRead>;
};


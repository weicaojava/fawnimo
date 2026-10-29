/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ShotGraphReceiptSummary } from './ShotGraphReceiptSummary';
/**
 * Downloadable original graph data; restoring it does not restore adopted qualification.
 */
export type ShotGraphReceiptRead = {
    project_id: string;
    chapter_id: string;
    receipt: ShotGraphReceiptSummary;
    before: Record<string, any>;
    after_fingerprint: string;
    recovery_note: string;
};


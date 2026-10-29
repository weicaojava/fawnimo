/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ShotGraphItem } from './ShotGraphItem';
import type { ShotGraphReceiptSummary } from './ShotGraphReceiptSummary';
/**
 * The full chapter order and exact compare-and-swap token.
 */
export type ShotGraphRead = {
    project_id: string;
    chapter_id: string;
    revision: number;
    fingerprint: string;
    shots: Array<ShotGraphItem>;
    receipts: Array<ShotGraphReceiptSummary>;
};


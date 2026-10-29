/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ShotGraphRead } from './ShotGraphRead';
import type { ShotGraphReceiptSummary } from './ShotGraphReceiptSummary';
/**
 * A committed command receipt alongside current state; replay never reapplies an old edit.
 */
export type ShotGraphResult = {
    receipt: ShotGraphReceiptSummary;
    replayed: boolean;
    graph: ShotGraphRead;
};


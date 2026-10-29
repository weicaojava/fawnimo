/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A durable backup pointer for structural recovery and request replay.
 */
export type ShotGraphReceiptSummary = {
    id: string;
    action: string;
    revision: number;
    result_shot_id: (string | null);
    source_shot_ids: Array<string>;
};


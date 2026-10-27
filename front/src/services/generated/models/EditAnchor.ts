/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One human declaration in original source milliseconds, not timeline time.
 */
export type EditAnchor = {
    kind: 'beat' | 'dialogue';
    item_id: string;
    take_id: string;
    start_ms: number;
    end_ms: number;
    confirmed?: boolean;
};


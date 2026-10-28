/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaSnapshotPayload } from './DnaSnapshotPayload';
/**
 * A content-addressed human hypothesis, not an original-author prompt.
 */
export type DnaSnapshot = {
    id: string;
    content_hash: string;
    payload: DnaSnapshotPayload;
};


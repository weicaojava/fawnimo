/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaVersionMedia } from './DnaVersionMedia';
/**
 * Exact owned production snapshot metadata; arbitrary M2 payload is excluded.
 */
export type DnaAssetVersion = {
    version_id: string;
    asset_id: string;
    kind: 'character' | 'scene' | 'prop';
    content_hash: string;
    media: Array<DnaVersionMedia>;
};


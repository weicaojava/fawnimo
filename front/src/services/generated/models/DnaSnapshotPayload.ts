/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaAssetVersion } from './DnaAssetVersion';
import type { DnaDocument_Output } from './DnaDocument_Output';
import type { DnaSource } from './DnaSource';
/**
 * Portable integer-only immutable DNA without disk paths, credentials or execution state.
 */
export type DnaSnapshotPayload = {
    schema_version?: number;
    source: DnaSource;
    document: DnaDocument_Output;
    asset_versions: Array<DnaAssetVersion>;
    human_declarations_only?: boolean;
};


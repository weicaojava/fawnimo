/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SoundDocument_Input } from './SoundDocument_Input';
/**
 * Save every sound field atomically under an expected saved revision.
 */
export type SoundPatch = {
    expected_revision: number;
    document: SoundDocument_Input;
};


/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaDocument_Input } from './DnaDocument_Input';
/**
 * Save the entire controlled document under optimistic revision protection.
 */
export type DnaPatch = {
    expected_revision: number;
    document: DnaDocument_Input;
};


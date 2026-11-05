"""Strict, versioned restart codec for trusted offline simulation policy intent.

No pickle, class imports from data, credentials, or persisted owner authorization.
Current observations are NEVER restored from the archive as current evidence.
"""
from dataclasses import fields, replace
from datetime import datetime
import hashlib
import json

from oneil_execution_v2.domain import Order, RiskPolicy, aware, require
from oneil_policy_v2.admission import PolicyAdmission, PolicyContext
from oneil_policy_v2.evidence import (Evidence, Controls, Position, AccountEvidence,
    MarketEvidence, SessionEvidence, Candidate, CandidateBatch, TechnicalExit)
from oneil_policy_v2.planner import Proposal, ManualAdd
from oneil_policy_v2.policy import TradingPolicy

_TYPES = {c.__name__: c for c in (Order, RiskPolicy, PolicyAdmission, PolicyContext,
    Evidence, Controls, Position, AccountEvidence, MarketEvidence, SessionEvidence,
    Candidate, CandidateBatch, TechnicalExit, Proposal, ManualAdd, TradingPolicy)}
_MAX_BYTES = 2 * 1024 * 1024


def _pack(value, depth=0):
    require(depth <= 32, 'INTENT_TOO_DEEP')
    kind = type(value)
    if value is None or kind in (str, int, bool):
        return value
    if kind is datetime:
        return {'type': 'datetime', 'value': aware(value).isoformat()}
    if kind.__name__ in _TYPES and _TYPES[kind.__name__] is kind:
        return {'type': kind.__name__, 'value': {f.name: _pack(getattr(value, f.name), depth+1)
                                                for f in fields(value)}}
    if kind in (tuple, frozenset, dict):
        if kind is dict:
            values = [[_pack(k, depth+1), _pack(v, depth+1)] for k, v in value.items()]
            values.sort(key=lambda x: json.dumps(x[0], sort_keys=True))
        else:
            values = [_pack(x, depth+1) for x in value]
            if kind is frozenset:
                values.sort(key=lambda x: json.dumps(x, sort_keys=True))
        return {'type': kind.__name__, 'value': values}
    require(False, 'INTENT_TYPE_UNSUPPORTED')


def _unpack(value, depth=0):
    require(depth <= 32, 'INTENT_TOO_DEEP')
    if value is None or type(value) in (str, int, bool):
        return value
    require(type(value) is dict and set(value) == {'type', 'value'}, 'INTENT_FORMAT_INVALID')
    tag, data = value['type'], value['value']
    require(type(tag) is str, 'INTENT_FORMAT_INVALID')
    if tag == 'datetime':
        require(type(data) is str, 'INTENT_FORMAT_INVALID')
        return aware(datetime.fromisoformat(data))
    if tag in _TYPES:
        cls = _TYPES[tag]
        require(type(data) is dict and set(data) == {f.name for f in fields(cls)}, 'INTENT_FIELDS_INVALID')
        return cls(**{k: _unpack(v, depth+1) for k, v in data.items()})
    require(tag in ('tuple', 'frozenset', 'dict') and type(data) is list, 'INTENT_TYPE_UNSUPPORTED')
    if tag == 'dict':
        result = {}
        for pair in data:
            require(type(pair) is list and len(pair) == 2, 'INTENT_FORMAT_INVALID')
            key, item = (_unpack(x, depth+1) for x in pair)
            require(key not in result, 'INTENT_DUPLICATE_KEY')
            result[key] = item
        return result
    items = [_unpack(x, depth+1) for x in data]
    if tag == 'frozenset':
        require(len(set(items)) == len(items), 'INTENT_DUPLICATE_KEY')
        return frozenset(items)
    return tuple(items)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'INTENT_DUPLICATE_KEY')
        result[key] = value
    return result


def encode_intent(admission):
    require(type(admission) is PolicyAdmission and type(admission.original_context) is PolicyContext,
            'POLICY_ADMISSION_REQUIRED')
    require(type(admission.proposal) is Proposal and type(admission.proposal.order) is Order,
            'POLICY_ORDER_MISMATCH')
    admission.proposal.order.validate()
    require(admission.mode in ('AUTOMATIC', 'MANUAL_ADD'), 'POLICY_INTENT_MODE_INVALID')
    # Save original intent/evidence only; stale current data is not a restart cache.
    original = replace(admission, context=admission.original_context)
    text = json.dumps({'version': 1, 'intent': _pack(original)}, sort_keys=True, separators=(',', ':'))
    require(len(text.encode()) <= _MAX_BYTES, 'INTENT_TOO_LARGE')
    return text, hashlib.sha256(text.encode()).hexdigest()


def decode_intent(text, digest, *, current_context):
    require(type(text) is str and len(text.encode()) <= _MAX_BYTES, 'INTENT_TOO_LARGE')
    require(hashlib.sha256(text.encode()).hexdigest() == digest, 'INTENT_DIGEST_MISMATCH')
    envelope = json.loads(text, object_pairs_hook=_pairs)
    require(type(envelope) is dict and set(envelope) == {'version', 'intent'}
            and type(envelope['version']) is int and envelope['version'] == 1, 'INTENT_VERSION_INVALID')
    original = _unpack(envelope['intent'])
    require(type(original) is PolicyAdmission and type(current_context) is PolicyContext,
            'EXPLICIT_CURRENT_POLICY_CONTEXT_REQUIRED')
    require(encode_intent(original) == (text, digest), 'INTENT_NONCANONICAL')
    require(current_context.policy == original.original_context.policy, 'POLICY_CHANGED')
    return replace(original, context=current_context)

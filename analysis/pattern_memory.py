"""
RAG (Retrieval-Augmented Generation) for Trading ML
Stores historical patterns and retrieves similar situations to enhance predictions.

Features:
- Embeds market patterns (OHLCV + indicators) into vectors
- Stores patterns with their outcomes (win/loss)
- Retrieves top-k similar patterns for new trades
- Provides historical context to boost AI decisions
"""

import numpy as np
import sqlite3
import pickle
import json
import os
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Optional
from collections import defaultdict

# Try to import sentence-transformers for better embeddings
try:
    from sentence_transformers import SentenceTransformer
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False

# Try to import faiss for fast vector search
try:
    import faiss
    HAS_FAISS = True
except ImportError:
    HAS_FAISS = False


class PatternMemory:
    """
    RAG-based pattern memory for trading.
    Stores historical patterns and retrieves similar ones for context.
    """
    
    def __init__(self, db_path: str = "pattern_memory.db", embedding_dim: int = 64):
        self.db_path = db_path
        self.embedding_dim = embedding_dim
        self.patterns = []  # In-memory cache
        self.embeddings = []  # In-memory embeddings
        
        # Initialize vector index
        if HAS_FAISS:
            self.index = faiss.IndexFlatL2(embedding_dim)
        else:
            self.index = None
            
        # Initialize embedding model (lightweight for trading)
        self.embedding_model = None
        if HAS_SENTENCE_TRANSFORMERS:
            try:
                # Use a small, fast model
                self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
            except:
                pass
        
        # Initialize database
        self._init_db()
        self._load_patterns()
        
        print(f"[RAG] PatternMemory initialized: {len(self.patterns)} patterns loaded")
        print(f"[RAG] FAISS: {'Enabled' if HAS_FAISS else 'Disabled (using numpy)'}")
        print(f"[RAG] Sentence Transformers: {'Enabled' if self.embedding_model else 'Disabled (using feature hash)'}")
    
    def _init_db(self):
        """Initialize SQLite database for pattern storage."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                direction TEXT NOT NULL,
                outcome TEXT,
                pnl REAL DEFAULT 0,
                features TEXT NOT NULL,
                embedding BLOB NOT NULL,
                context TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_symbol ON patterns(symbol)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_outcome ON patterns(outcome)
        ''')
        
        conn.commit()
        conn.close()
    
    def _load_patterns(self):
        """Load patterns from database into memory."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, symbol, direction, outcome, pnl, features, embedding, context
            FROM patterns ORDER BY id DESC LIMIT 10000
        ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        self.patterns = []
        self.embeddings = []
        
        for row in rows:
            pattern = {
                'id': row[0],
                'symbol': row[1],
                'direction': row[2],
                'outcome': row[3],
                'pnl': row[4],
                'features': json.loads(row[5]),
                'context': row[7]
            }
            embedding = pickle.loads(row[6])
            
            self.patterns.append(pattern)
            self.embeddings.append(embedding)
        
        # Build FAISS index
        if self.embeddings and HAS_FAISS:
            embeddings_array = np.array(self.embeddings, dtype=np.float32)
            self.index = faiss.IndexFlatL2(self.embedding_dim)
            self.index.add(embeddings_array)
    
    def _extract_features(self, df, symbol: str) -> Dict:
        """Extract key features from market data for pattern matching."""
        if df is None or len(df) < 20:
            return {}
        
        last = df.iloc[-1]
        features = {}
        
        # Price action features
        features['price_change_5'] = (df['close'].iloc[-1] / df['close'].iloc[-5] - 1) * 100
        features['price_change_20'] = (df['close'].iloc[-1] / df['close'].iloc[-20] - 1) * 100
        
        # Volatility
        if 'atr' in df.columns:
            features['atr_pct'] = (last['atr'] / last['close']) * 100
        
        # Trend indicators
        if 'rsi' in df.columns:
            features['rsi'] = float(last['rsi'])
        if 'adx' in df.columns:
            features['adx'] = float(last['adx'])
        if 'macd' in df.columns:
            features['macd'] = float(last['macd'])
        
        # Moving average position
        if 'sma_20' in df.columns:
            features['price_vs_sma20'] = (last['close'] / last['sma_20'] - 1) * 100
        if 'sma_50' in df.columns:
            features['price_vs_sma50'] = (last['close'] / last['sma_50'] - 1) * 100
        
        # Volume analysis
        if 'tick_volume' in df.columns:
            avg_vol = df['tick_volume'].rolling(20).mean().iloc[-1]
            features['volume_ratio'] = last['tick_volume'] / avg_vol if avg_vol > 0 else 1
        
        # Candle patterns
        body = abs(last['close'] - last['open'])
        range_hl = last['high'] - last['low']
        features['body_ratio'] = body / range_hl if range_hl > 0 else 0
        features['upper_wick'] = (last['high'] - max(last['open'], last['close'])) / range_hl if range_hl > 0 else 0
        features['lower_wick'] = (min(last['open'], last['close']) - last['low']) / range_hl if range_hl > 0 else 0
        
        # Symbol type
        features['symbol'] = symbol
        
        return features
    
    def _create_embedding(self, features: Dict) -> np.ndarray:
        """Create embedding vector from features."""
        if self.embedding_model and features:
            # Use sentence transformer to embed feature description
            feature_text = self._features_to_text(features)
            try:
                embedding = self.embedding_model.encode(feature_text)
                # Reduce to target dimension
                if len(embedding) > self.embedding_dim:
                    embedding = embedding[:self.embedding_dim]
                elif len(embedding) < self.embedding_dim:
                    embedding = np.pad(embedding, (0, self.embedding_dim - len(embedding)))
                return embedding.astype(np.float32)
            except:
                pass
        
        # Fallback: Create embedding from numerical features
        embedding = np.zeros(self.embedding_dim, dtype=np.float32)
        
        # Map features to embedding dimensions
        feature_mapping = [
            ('price_change_5', 0, 0.1),
            ('price_change_20', 1, 0.05),
            ('atr_pct', 2, 1.0),
            ('rsi', 3, 0.01),
            ('adx', 4, 0.02),
            ('macd', 5, 0.1),
            ('price_vs_sma20', 6, 0.1),
            ('price_vs_sma50', 7, 0.05),
            ('volume_ratio', 8, 0.5),
            ('body_ratio', 9, 1.0),
            ('upper_wick', 10, 1.0),
            ('lower_wick', 11, 1.0),
        ]
        
        for feat_name, idx, scale in feature_mapping:
            if feat_name in features and idx < self.embedding_dim:
                embedding[idx] = float(features[feat_name]) * scale
        
        # Normalize
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        
        return embedding
    
    def _features_to_text(self, features: Dict) -> str:
        """Convert features to text description for embedding."""
        parts = []
        
        if 'symbol' in features:
            parts.append(f"Symbol: {features['symbol']}")
        
        if 'rsi' in features:
            rsi = features['rsi']
            if rsi > 70:
                parts.append("RSI overbought")
            elif rsi < 30:
                parts.append("RSI oversold")
            else:
                parts.append("RSI neutral")
        
        if 'price_change_5' in features:
            pc = features['price_change_5']
            if pc > 1:
                parts.append("Strong upward momentum")
            elif pc < -1:
                parts.append("Strong downward momentum")
            else:
                parts.append("Sideways movement")
        
        if 'adx' in features:
            adx = features['adx']
            if adx > 25:
                parts.append("Strong trend")
            else:
                parts.append("Weak trend or ranging")
        
        if 'volume_ratio' in features:
            vr = features['volume_ratio']
            if vr > 1.5:
                parts.append("High volume")
            elif vr < 0.5:
                parts.append("Low volume")
        
        return ". ".join(parts) if parts else "Market pattern"
    
    def store_pattern(self, symbol: str, df, direction: str, 
                      outcome: str = None, pnl: float = 0,
                      context: str = None) -> int:
        """
        Store a trading pattern in the RAG memory.
        
        Args:
            symbol: Trading symbol
            df: DataFrame with OHLCV + indicators
            direction: BUY or SELL
            outcome: WIN, LOSS, or None (pending)
            pnl: Profit/loss amount
            context: Additional context text
        
        Returns:
            Pattern ID
        """
        features = self._extract_features(df, symbol)
        if not features:
            return -1
        
        embedding = self._create_embedding(features)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO patterns (symbol, timestamp, direction, outcome, pnl, features, embedding, context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            symbol,
            datetime.now(timezone.utc).isoformat(),
            direction,
            outcome,
            pnl,
            json.dumps(features),
            pickle.dumps(embedding),
            context
        ))
        
        pattern_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Update in-memory cache
        pattern = {
            'id': pattern_id,
            'symbol': symbol,
            'direction': direction,
            'outcome': outcome,
            'pnl': pnl,
            'features': features,
            'context': context
        }
        self.patterns.insert(0, pattern)
        self.embeddings.insert(0, embedding)
        
        # Update FAISS index
        if HAS_FAISS:
            self.index.add(embedding.reshape(1, -1))
        
        return pattern_id
    
    def update_outcome(self, pattern_id: int, outcome: str, pnl: float):
        """Update pattern outcome after trade closes."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE patterns SET outcome = ?, pnl = ? WHERE id = ?
        ''', (outcome, pnl, pattern_id))
        
        conn.commit()
        conn.close()
        
        # Update cache
        for pattern in self.patterns:
            if pattern['id'] == pattern_id:
                pattern['outcome'] = outcome
                pattern['pnl'] = pnl
                break
    
    def retrieve_similar(self, df, symbol: str, direction: str, 
                         k: int = 5) -> List[Dict]:
        """
        Retrieve k most similar historical patterns.
        
        Args:
            df: Current market data
            symbol: Trading symbol
            direction: Intended trade direction
            k: Number of similar patterns to retrieve
        
        Returns:
            List of similar patterns with their outcomes
        """
        if not self.patterns:
            return []
        
        features = self._extract_features(df, symbol)
        if not features:
            return []
        
        query_embedding = self._create_embedding(features)
        
        # Search for similar patterns
        if HAS_FAISS and self.index.ntotal > 0:
            distances, indices = self.index.search(
                query_embedding.reshape(1, -1), 
                min(k * 2, self.index.ntotal)  # Get more to filter
            )
            similar_indices = indices[0].tolist()
        else:
            # Numpy fallback
            if not self.embeddings:
                return []
            embeddings_array = np.array(self.embeddings)
            distances = np.linalg.norm(embeddings_array - query_embedding, axis=1)
            similar_indices = np.argsort(distances)[:k * 2].tolist()
        
        # Filter by direction and outcome
        results = []
        for idx in similar_indices:
            if idx >= len(self.patterns):
                continue
            pattern = self.patterns[idx]
            
            # Prefer same direction patterns
            if pattern['direction'] == direction:
                results.append(pattern.copy())
            
            if len(results) >= k:
                break
        
        # If not enough same-direction, add any
        if len(results) < k:
            for idx in similar_indices:
                if idx >= len(self.patterns):
                    continue
                pattern = self.patterns[idx]
                if pattern not in results:
                    results.append(pattern.copy())
                if len(results) >= k:
                    break
        
        return results
    
    def get_pattern_context(self, df, symbol: str, direction: str) -> Dict:
        """
        Get RAG context for a trading decision.
        
        Returns:
            Dict with:
                - similar_patterns: List of similar historical patterns
                - historical_win_rate: Win rate of similar patterns
                - avg_pnl: Average PnL of similar patterns
                - recommendation: PROCEED, CAUTION, or AVOID
                - confidence_boost: Multiplier for AI confidence
        """
        similar = self.retrieve_similar(df, symbol, direction, k=10)
        
        if not similar:
            return {
                'similar_patterns': [],
                'historical_win_rate': 0.5,
                'avg_pnl': 0,
                'recommendation': 'NEUTRAL',
                'confidence_boost': 1.0,
                'context_text': "No similar patterns found"
            }
        
        # Calculate statistics from similar patterns
        wins = sum(1 for p in similar if p.get('outcome') == 'WIN')
        losses = sum(1 for p in similar if p.get('outcome') == 'LOSS')
        total_closed = wins + losses
        
        win_rate = wins / total_closed if total_closed > 0 else 0.5
        avg_pnl = np.mean([p.get('pnl', 0) for p in similar if p.get('outcome')])
        
        # Determine recommendation
        if win_rate >= 0.7 and total_closed >= 3:
            recommendation = 'PROCEED'
            confidence_boost = 1.2
        elif win_rate <= 0.3 and total_closed >= 3:
            recommendation = 'AVOID'
            confidence_boost = 0.5
        elif win_rate >= 0.5:
            recommendation = 'NEUTRAL'
            confidence_boost = 1.0
        else:
            recommendation = 'CAUTION'
            confidence_boost = 0.8
        
        # Build context text
        context_parts = [
            f"Found {len(similar)} similar historical patterns.",
            f"Historical win rate: {win_rate*100:.1f}% ({wins}W/{losses}L)",
            f"Average PnL: ${avg_pnl:.2f}",
            f"RAG Recommendation: {recommendation}"
        ]
        
        return {
            'similar_patterns': similar[:5],  # Top 5 for display
            'historical_win_rate': win_rate,
            'avg_pnl': avg_pnl,
            'recommendation': recommendation,
            'confidence_boost': confidence_boost,
            'context_text': " | ".join(context_parts)
        }
    
    def get_stats(self) -> Dict:
        """Get memory statistics."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM patterns')
        total = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM patterns WHERE outcome = "WIN"')
        wins = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM patterns WHERE outcome = "LOSS"')
        losses = cursor.fetchone()[0]
        
        cursor.execute('SELECT symbol, COUNT(*) FROM patterns GROUP BY symbol')
        by_symbol = dict(cursor.fetchall())
        
        conn.close()
        
        return {
            'total_patterns': total,
            'wins': wins,
            'losses': losses,
            'win_rate': wins / (wins + losses) if (wins + losses) > 0 else 0,
            'by_symbol': by_symbol
        }


# Global singleton instance
_pattern_memory = None

def get_pattern_memory() -> PatternMemory:
    """Get or create the global PatternMemory instance."""
    global _pattern_memory
    if _pattern_memory is None:
        _pattern_memory = PatternMemory()
    return _pattern_memory
